"""Gemini summarisation via the REST API (free tier friendly, single call per run)."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import requests

from .errors import GeminiAuthError, GeminiError, GeminiQuotaError, TransientError
from .models import Story
from .retry import retry_call

log = logging.getLogger(__name__)

ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
TIMEOUT = (5, 90)
MAX_BULLETS = 3
MAX_BULLET_CHARS = 160

PROMPT = """You are an analyst writing a daily AI industry briefing.
Below are numbered news items, each with a title, source and excerpt.
For EACH item write at most three very short bullets (max 20 words each):
  1. what happened
  2. why it matters
  3. potential impact or what is next (omit if the text does not support it)

Strict rules:
- Use ONLY facts present in the title and excerpt. Do not add numbers, names, dates or quotes
  that are not in the text. If the excerpt is thin, give fewer bullets rather than guessing.
- No jargon, no repetition of the title, no filler.
- Plain text only. No markdown, no bullet symbols, no links, no emojis.
- Respond with a JSON array only: [{{"id": <number>, "bullets": ["<text>", ...]}}, ...]
- Include every id exactly once.

Items:
{items}
"""

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.I)


def build_prompt(stories: list[Story]) -> str:
    blocks = [
        f"[{i}] Title: {s.title}\nSource: {s.source}\nExcerpt: {s.snippet or '(none)'}"
        for i, s in enumerate(stories, 1)
    ]
    return PROMPT.format(items="\n\n".join(blocks))


def _clean_bullets(row: dict[str, Any]) -> list[str]:
    raw = row.get("bullets")
    if raw is None and isinstance(row.get("summary"), str):
        raw = [row["summary"]]  # tolerate a single-string answer
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        clean = " ".join(item.split())
        if not clean:
            continue
        if len(clean) > MAX_BULLET_CHARS:
            clean = clean[: MAX_BULLET_CHARS - 1].rstrip() + "…"
        out.append(clean)
    return out[:MAX_BULLETS]


def parse_summaries(text: str, valid_ids: set[int]) -> dict[int, list[str]]:
    """Parse Gemini's JSON output, keeping only well-formed bullets for known ids."""
    try:
        data = json.loads(_FENCE.sub("", text.strip()))
    except json.JSONDecodeError:
        raise TransientError("Gemini returned malformed JSON") from None
    if not isinstance(data, list):
        raise TransientError("Gemini JSON was not a list")
    out: dict[int, list[str]] = {}
    for row in data:
        if not isinstance(row, dict):
            continue
        sid = row.get("id")
        if isinstance(sid, int) and sid in valid_ids:
            bullets = _clean_bullets(row)
            if bullets:
                out.setdefault(sid, bullets)
    if not out:
        raise TransientError("Gemini response contained no usable summaries")
    return out


def _error_details(resp: requests.Response) -> tuple[str, str]:
    try:
        err = resp.json().get("error", {})
    except ValueError:
        return "", ""
    return str(err.get("status", "")), json.dumps(err)[:2000]


def _retry_delay(resp: requests.Response) -> float | None:
    header = resp.headers.get("Retry-After")
    if header and header.isdigit():
        return float(header)
    match = re.search(r'"retryDelay":\s*"(\d+)', resp.text)
    return float(match.group(1)) if match else None


def _classify(resp: requests.Response, model: str) -> None:
    code = resp.status_code
    if code == 200:
        return
    status, details = _error_details(resp)
    log.error("Gemini HTTP %d (%s): %s", code, status, details[:500])
    if code in (400, 401, 403) and (
        "API_KEY" in details or code in (401, 403) or status == "PERMISSION_DENIED"
    ):
        raise GeminiAuthError(
            "Gemini rejected the API key (invalid, expired or lacks access). "
            "Create a new key at https://aistudio.google.com/apikey and update GEMINI_API_KEY."
        )
    if code == 404:
        raise GeminiError(f"Gemini model '{model}' not found. Set GEMINI_MODEL to a valid model.")
    if code == 429:
        if "PerDay" in details or "per day" in details.lower():
            raise GeminiQuotaError(
                "Gemini daily free-tier quota exhausted. It resets daily; "
                "re-run later or use a different GEMINI_MODEL."
            )
        raise TransientError("Gemini rate limit (429)", retry_after=_retry_delay(resp))
    if code >= 500:
        raise TransientError(f"Gemini server error {code}")
    raise GeminiError(f"Gemini request rejected (HTTP {code}).")


def summarize(
    stories: list[Story],
    *,
    api_key: str,
    model: str,
    session: requests.Session | None = None,
) -> list[Story]:
    """Attach Gemini summaries. Stories Gemini skipped are dropped, never invented."""
    if not stories:
        return []
    session = session or requests.Session()
    ids = set(range(1, len(stories) + 1))
    body: dict[str, Any] = {
        "contents": [{"parts": [{"text": build_prompt(stories)}]}],
        "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"},
    }

    def attempt() -> dict[int, list[str]]:
        try:
            resp = session.post(
                ENDPOINT.format(model=model),
                json=body,
                headers={"x-goog-api-key": api_key},
                timeout=TIMEOUT,
            )
        except (requests.Timeout, requests.ConnectionError) as exc:
            raise TransientError(f"Gemini network error: {type(exc).__name__}") from None
        _classify(resp, model)
        try:
            payload = resp.json()
            parts = payload["candidates"][0]["content"]["parts"]
            text = "".join(p.get("text", "") for p in parts)
        except (ValueError, KeyError, IndexError, TypeError):
            reason = ""
            if isinstance(payload, dict):
                reason = str(payload.get("promptFeedback", {}).get("blockReason", ""))
            raise TransientError(f"Gemini returned no content {reason}".strip()) from None
        return parse_summaries(text, ids)

    try:
        summaries = retry_call(attempt, what="Gemini summarisation", attempts=4, base_delay=5.0)
    except TransientError as exc:
        raise GeminiError(f"Gemini unavailable after retries: {exc}") from exc

    result = []
    for i, story in enumerate(stories, 1):
        if i in summaries:
            story.summary = "\n".join(summaries[i])
            result.append(story)
        else:
            log.warning("Gemini gave no summary for %r; omitting it", story.title)
    return result
