"""Telegram formatting, message splitting and delivery."""

from __future__ import annotations

import html
import logging
from datetime import datetime
from typing import Any

import requests

from .errors import TelegramError, TransientError
from .models import Story
from .retry import retry_call

log = logging.getLogger(__name__)

API = "https://api.telegram.org/bot{token}/{method}"
LIMIT = 4096
SAFE_LIMIT = 3800  # headroom; Telegram counts characters after entity parsing
TIMEOUT = (5, 30)


def _trim(text: str, n: int) -> str:
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def format_story(index: int, story: Story) -> str:
    title = html.escape(_trim(story.title, 200))
    url = html.escape(story.url, quote=True)
    meta = html.escape(story.source)
    if story.published:
        meta += f" · {story.published:%d %b %Y}"
    lines = [f'<b>{index}. <a href="{url}">{title}</a></b>', f"<i>{meta}</i>"]
    if story.summary:
        lines.append(html.escape(_trim(story.summary, 600)))
    return "\n".join(lines)


def split_messages(header: str, blocks: list[str], limit: int = SAFE_LIMIT) -> list[str]:
    """Pack blocks into messages <= limit chars without cutting through any block."""
    messages: list[str] = []
    current = header
    for block in blocks:
        block = _trim(block, limit) if len(block) > limit else block
        candidate = f"{current}\n\n{block}" if current else block
        if len(candidate) > limit and current:
            messages.append(current)
            current = block
        else:
            current = candidate
    if current:
        messages.append(current)
    return messages


def format_digest(stories: list[Story], when: datetime) -> list[str]:
    header = f"<b>🧠 AI Market Intelligence · {when:%a %d %b %Y}</b>\nTop {len(stories)} updates"
    blocks = [format_story(i, s) for i, s in enumerate(stories, 1)]
    return split_messages(header, blocks)


class TelegramClient:
    def __init__(self, token: str, chat_id: str, session: requests.Session | None = None) -> None:
        self._token = token
        self._chat_id = chat_id
        self._session = session or requests.Session()

    def _send_once(self, text: str) -> None:
        payload: dict[str, Any] = {
            "chat_id": self._chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        try:
            resp = self._session.post(
                API.format(token=self._token, method="sendMessage"), json=payload, timeout=TIMEOUT
            )
        except (requests.Timeout, requests.ConnectionError) as exc:
            # Exception text can embed the URL (and thus the token); log the type only.
            raise TransientError(f"Telegram network error: {type(exc).__name__}") from None
        if resp.status_code == 200:
            return
        try:
            body = resp.json()
        except ValueError:
            body = {}
        desc = str(body.get("description", ""))[:300]
        if resp.status_code == 429:
            wait = body.get("parameters", {}).get("retry_after")
            raise TransientError("Telegram rate limit (429)", retry_after=float(wait or 5))
        if resp.status_code >= 500:
            raise TransientError(f"Telegram server error {resp.status_code}")
        hint = {
            401: "Bot token is invalid. Re-check TELEGRAM_BOT_TOKEN.",
            403: "Bot cannot post there. Add the bot to the group (and make it admin if needed).",
            400: "Telegram rejected the request (check TELEGRAM_CHAT_ID; supergroup ids start with -100).",
            404: "Bot token not recognised. Re-check TELEGRAM_BOT_TOKEN.",
        }.get(resp.status_code, "Unexpected Telegram response.")
        raise TelegramError(f"HTTP {resp.status_code}: {desc}. {hint}")

    def send(self, text: str) -> None:
        retry_call(lambda: self._send_once(text), what="Telegram sendMessage", base_delay=2.0)

    def send_all(self, messages: list[str]) -> None:
        for i, msg in enumerate(messages, 1):
            self.send(msg)
            log.info("Sent Telegram message %d/%d", i, len(messages))
