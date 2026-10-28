"""RSS collection and normalisation."""

from __future__ import annotations

import calendar
import html
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import feedparser
import requests

from .errors import CollectionError, TransientError
from .models import Story
from .retry import retry_call

log = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (compatible; ai-market-agent/1.0; +https://github.com)"
TIMEOUT = (5, 20)  # connect, read seconds


@dataclass(frozen=True)
class Feed:
    name: str
    url: str
    weight: float  # 0..1 trust/importance of the source
    official: bool = False  # first-party announcement
    ai_only: bool = False  # every item is AI related; skip keyword relevance filter


FEEDS: tuple[Feed, ...] = (
    Feed("OpenAI", "https://openai.com/news/rss.xml", 1.0, True, True),
    Feed("Google DeepMind", "https://deepmind.google/blog/rss.xml", 1.0, True, True),
    Feed("Google AI", "https://blog.google/technology/ai/rss/", 0.9, True, True),
    Feed("Hugging Face", "https://huggingface.co/blog/feed.xml", 0.8, True, True),
    Feed("NVIDIA", "https://blogs.nvidia.com/feed/", 0.7, True),
    Feed("AWS Machine Learning", "https://aws.amazon.com/blogs/machine-learning/feed/", 0.5, True, True),
    Feed("TechCrunch", "https://techcrunch.com/category/artificial-intelligence/feed/", 0.8, False, True),
    Feed("The Verge", "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml", 0.8, False, True),
    Feed("MIT Technology Review", "https://www.technologyreview.com/topic/artificial-intelligence/feed", 0.8, False, True),
    Feed("Ars Technica", "https://arstechnica.com/ai/feed/", 0.7, False, True),
)  # fmt: skip

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")
_TRACKING = ("utm_", "fbclid", "gclid", "mc_", "ref", "cmpid", "source")


def clean_text(raw: str, limit: int = 600) -> str:
    text = html.unescape(_TAG.sub(" ", raw or ""))
    text = _WS.sub(" ", text).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def canonical_url(url: str) -> str:
    """Strip tracking params, fragments and trailing slashes so duplicates compare equal."""
    parts = urlsplit(url.strip())
    query = [
        (k, v) for k, v in parse_qsl(parts.query) if not k.lower().startswith(_TRACKING)
    ]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(query), ""))


def _entry_date(entry: feedparser.FeedParserDict) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        parsed = entry.get(key)
        if parsed:
            return datetime.fromtimestamp(calendar.timegm(parsed), tz=UTC)
    return None


def parse_feed(content: bytes, feed: Feed) -> list[Story]:
    """Normalise raw feed XML into Story objects. Entries without title/link are dropped."""
    parsed = feedparser.parse(content)
    if parsed.bozo and not parsed.entries:
        raise CollectionError(f"{feed.name}: feed could not be parsed")
    stories: list[Story] = []
    for entry in parsed.entries:
        title = clean_text(str(entry.get("title", "")), 300)
        link = str(entry.get("link", "")).strip()
        if not title or urlsplit(link).scheme not in ("http", "https"):
            continue
        body = entry.get("summary") or ""
        if not body and entry.get("content"):
            body = entry["content"][0].get("value", "")
        stories.append(
            Story(
                title=title,
                url=canonical_url(link),
                source=feed.name,
                published=_entry_date(entry),
                snippet=clean_text(str(body)),
                source_weight=feed.weight,
                official=feed.official,
                ai_only=feed.ai_only,
            )
        )
    return stories


def fetch_feed(session: requests.Session, feed: Feed) -> bytes:
    def attempt() -> bytes:
        try:
            resp = session.get(feed.url, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})
        except (requests.Timeout, requests.ConnectionError) as exc:
            raise TransientError(f"{feed.name}: {type(exc).__name__}") from None
        if resp.status_code == 429 or resp.status_code >= 500:
            raise TransientError(f"{feed.name}: HTTP {resp.status_code}")
        if resp.status_code != 200:
            raise CollectionError(f"{feed.name}: HTTP {resp.status_code}")
        return resp.content

    return retry_call(attempt, what=f"fetch {feed.name}", attempts=3, base_delay=1.5)


def collect(
    feeds: tuple[Feed, ...] = FEEDS,
    *,
    session: requests.Session | None = None,
    now: datetime | None = None,
    lookback_hours: int = 36,
    fetcher: Callable[[requests.Session, Feed], bytes] = fetch_feed,
) -> tuple[list[Story], int]:
    """Fetch all feeds; tolerate individual failures. Returns (recent stories, feeds ok)."""
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(hours=lookback_hours)
    session = session or requests.Session()
    stories: list[Story] = []
    ok = 0
    for feed in feeds:
        try:
            items = parse_feed(fetcher(session, feed), feed)
        except (TransientError, CollectionError) as exc:
            log.warning("Skipping feed %s: %s", feed.name, exc)
            continue
        ok += 1
        recent = [
            s for s in items
            if s.published is not None and cutoff <= s.published <= now + timedelta(hours=1)
        ]
        log.info("%s: %d items, %d within window", feed.name, len(items), len(recent))
        stories.extend(recent)
    if ok == 0:
        raise CollectionError("All news feeds failed; check network access and feed URLs.")
    return stories, ok
