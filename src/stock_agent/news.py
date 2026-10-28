"""Indian market news from RSS, reusing the news agent's feed fetching and parsing."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import requests

from ai_market_agent import sources
from ai_market_agent.errors import CollectionError, TransientError
from ai_market_agent.models import Story
from ai_market_agent.sources import Feed

log = logging.getLogger(__name__)

FEEDS: tuple[Feed, ...] = (
    Feed("Economic Times Markets", "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms", 0.9),
    Feed("Moneycontrol", "https://www.moneycontrol.com/rss/business.xml", 0.8),
    Feed("Mint Markets", "https://www.livemint.com/rss/markets", 0.8),
    Feed("Business Standard Markets", "https://www.business-standard.com/rss/markets-106.rss", 0.8),
)  # fmt: skip

# Higher weight = more likely to matter to an Indian market reader.
_KEYWORDS: tuple[tuple[re.Pattern[str], float], ...] = tuple(
    (re.compile(p, re.I), w)
    for p, w in (
        (r"\b(nifty|sensex|bank nifty)\b", 3),
        (r"\b(rbi|repo rate|inflation|gdp|fii|dii|fpi|sebi|budget|rupee)\b", 3),
        (r"\b(q[1-4]|results?|earnings|profit|dividend|buyback|ipo|order win|merger|acquisition)\b", 2),
        (r"\b(crude|oil|fed|dollar|gold)\b", 1),
    )
)  # fmt: skip


def score(story: Story, now: datetime) -> float:
    text = f"{story.title} {story.snippet}"
    pts = sum(w for pat, w in _KEYWORDS if pat.search(text))
    age_h = max((now - story.published).total_seconds() / 3600, 0) if story.published else 48
    return pts * story.source_weight + max(0.0, 2 - age_h / 12)


def collect(
    *,
    lookback_hours: int,
    max_items: int,
    now: datetime | None = None,
    session: requests.Session | None = None,
    feeds: tuple[Feed, ...] = FEEDS,
    fetcher: Callable[[requests.Session, Feed], bytes] = sources.fetch_feed,
) -> list[Story]:
    """Recent, relevant, de-duplicated headlines. Feed failures are tolerated, all-fail raises."""
    now = now or datetime.now(UTC)
    session = session or requests.Session()
    cutoff = now - timedelta(hours=lookback_hours)
    items: list[Story] = []
    ok = 0
    for feed in feeds:
        try:
            parsed = sources.parse_feed(fetcher(session, feed), feed)
        except (TransientError, CollectionError) as exc:
            log.warning("Skipping feed %s: %s", feed.name, exc)
            continue
        ok += 1
        items.extend(s for s in parsed if s.published and cutoff <= s.published <= now)
    if ok == 0:
        raise CollectionError("All market news feeds failed.")
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    unique: list[Story] = []
    for story in items:
        key = re.sub(r"\W+", " ", story.title.lower()).strip()
        if story.url in seen_urls or key in seen_titles:
            continue
        seen_urls.add(story.url)
        seen_titles.add(key)
        story.score = score(story, now)
        unique.append(story)
    relevant = [s for s in unique if s.score >= 2]
    relevant.sort(key=lambda s: s.score, reverse=True)
    return relevant[:max_items]
