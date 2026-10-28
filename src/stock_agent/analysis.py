"""Gemini interpretation of already-verified data. It may explain, never add facts."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

import requests

from ai_market_agent import gemini
from ai_market_agent.errors import GeminiError, TransientError
from ai_market_agent.retry import retry_call

log = logging.getLogger(__name__)

MAX_POINTS = 4
MAX_CHARS = 220
_NUM = re.compile(r"\d[\d,]*\.?\d*")

PROMPT = """You are a cautious market analyst writing the interpretation section of a pre-market
note for Indian equities. Below are VERIFIED DATA (previous-session closes) and numbered HEADLINES.

Rules:
- Use ONLY the data and headlines below. Never add prices, percentages, dates, company facts or
  events that are not written below. If you cannot explain a move from the headlines, say the
  cause is not clear from the available information.
- Present interpretation as possibility, not certainty ("may", "suggests"). No predictions, no
  price targets, no buy/sell/hold advice.
- Each point at most 30 words, plain text, no markdown, no emojis.
- Respond with JSON only:
  {{"market_view": ["<point>", ...up to 4], "headline_notes": [{{"id": <n>, "note": "<max 20 words: why it may matter>"}}]}}

VERIFIED DATA:
{data}

HEADLINES:
{headlines}
"""


@dataclass
class Interpretation:
    market_view: list[str] = field(default_factory=list)
    notes: dict[int, str] = field(default_factory=dict)


def _numbers(text: str) -> set[str]:
    return {m.rstrip(".,").replace(",", "") for m in _NUM.findall(text)}


def parse(text: str, allowed_numbers: set[str], valid_ids: set[int]) -> Interpretation:
    """Keep well-formed items; drop any sentence quoting a number that is not in the input."""
    try:
        data = json.loads(gemini._FENCE.sub("", text.strip()))
    except json.JSONDecodeError:
        raise TransientError("Gemini returned malformed JSON") from None
    if not isinstance(data, dict):
        raise TransientError("Gemini JSON was not an object")

    def clean(raw: object) -> str:
        if not isinstance(raw, str):
            return ""
        out = " ".join(raw.split())
        if _numbers(out) - allowed_numbers:
            log.warning("Dropping AI text with unsupported numbers: %r", out[:80])
            return ""
        return out if len(out) <= MAX_CHARS else out[: MAX_CHARS - 1].rstrip() + "…"

    result = Interpretation()
    points = data.get("market_view")
    if isinstance(points, list):
        result.market_view = [p for p in (clean(x) for x in points) if p][:MAX_POINTS]
    notes = data.get("headline_notes")
    if isinstance(notes, list):
        for row in notes:
            if isinstance(row, dict) and isinstance(row.get("id"), int) and row["id"] in valid_ids:
                note = clean(row.get("note"))
                if note:
                    result.notes.setdefault(row["id"], note)
    return result


def interpret(
    data_block: str,
    headlines: list[str],
    *,
    api_key: str,
    model: str,
    session: requests.Session | None = None,
) -> Interpretation:
    """One Gemini call. Raises GeminiError if unavailable; callers should degrade gracefully."""
    session = session or requests.Session()
    numbered = "\n".join(f"[{i}] {h}" for i, h in enumerate(headlines, 1)) or "(none)"
    prompt = PROMPT.format(data=data_block, headlines=numbered)
    allowed = _numbers(data_block + "\n" + numbered)
    ids = set(range(1, len(headlines) + 1))
    body: dict[str, Any] = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"},
    }

    def attempt() -> Interpretation:
        try:
            resp = session.post(
                gemini.ENDPOINT.format(model=model),
                json=body,
                headers={"x-goog-api-key": api_key},
                timeout=gemini.TIMEOUT,
            )
        except (requests.Timeout, requests.ConnectionError) as exc:
            raise TransientError(f"Gemini network error: {type(exc).__name__}") from None
        gemini._classify(resp, model)
        try:
            parts = resp.json()["candidates"][0]["content"]["parts"]
            text = "".join(p.get("text", "") for p in parts)
        except (ValueError, KeyError, IndexError, TypeError):
            raise TransientError("Gemini returned no content") from None
        return parse(text, allowed, ids)

    try:
        return retry_call(attempt, what="Gemini interpretation", attempts=4, base_delay=5.0)
    except TransientError as exc:
        raise GeminiError(f"Gemini unavailable after retries: {exc}") from exc
