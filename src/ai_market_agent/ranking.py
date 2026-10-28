"""Relevance filtering, de-duplication and ranking."""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime

from .models import Story

RELEVANCE = re.compile(
    r"\b(ai|a\.i\.|artificial intelligence|llm|llms|gpt|gemini|claude|llama|mistral|openai|"
    r"anthropic|deepmind|machine learning|neural|generative|genai|chatbot|agentic|agents?|"
    r"diffusion|transformer|foundation model|copilot|nvidia|gpu|inference|robotics)\b",
    re.I,
)
# (pattern, weight): signals of a significant development
IMPORTANCE: tuple[tuple[re.Pattern[str], float], ...] = tuple(
    (re.compile(p, re.I), w)
    for p, w in (
        (r"\b(launch(es|ed)?|releas(es|ed|ing)|introduc(es|ed|ing)|unveil(s|ed)?|announc(es|ed))\b", 1.0),
        (r"\b(raises?|raised|funding|series [a-f]|valuation|acquir(es|ed|ition)|acquires|ipo)\b", 0.9),
        (r"\b(open[- ]source|open[- ]weights?|new model|state[- ]of[- ]the[- ]art|benchmark)\b", 0.8),
        (r"\b(research|paper|breakthrough|study)\b", 0.5),
        (r"\b(regulation|lawsuit|ban|policy|safety)\b", 0.5),
    )
)  # fmt: skip
_WORDS = re.compile(r"[a-z0-9]+")
_STOP = frozenset(["the", "a", "an", "of", "to", "in", "for", "and", "on", "with", "is", "are", "at", "by", "from", "as", "it", "its", "new", "how", "why"])
SIMILARITY = 0.6
PER_SOURCE_CAP = 3  # keeps one prolific outlet from dominating the digest


def is_relevant(story: Story) -> bool:
    return story.ai_only or bool(RELEVANCE.search(f"{story.title} {story.snippet}"))


def _tokens(title: str) -> frozenset[str]:
    return frozenset(w for w in _WORDS.findall(title.lower()) if w not in _STOP)


def _similar(a: frozenset[str], b: frozenset[str]) -> bool:
    if not a or not b:
        return False
    return len(a & b) / len(a | b) >= SIMILARITY


def dedupe(stories: list[Story]) -> list[Story]:
    """Merge duplicates (same URL or near-identical title). Keeps the highest-trust copy."""
    ordered = sorted(stories, key=lambda s: (-s.source_weight, -int(s.official)))
    kept: list[Story] = []
    tokens: list[frozenset[str]] = []
    for story in ordered:
        tok = _tokens(story.title)
        for i, existing in enumerate(kept):
            if existing.url == story.url or _similar(tok, tokens[i]):
                if story.source != existing.source and story.source not in existing.also_covered_by:
                    existing.also_covered_by.append(story.source)
                break
        else:
            kept.append(story)
            tokens.append(tok)
    return kept


def score(story: Story, now: datetime) -> float:
    """Blend of importance signals, source trust, recency (24h half-life) and coverage."""
    text = f"{story.title} {story.snippet}"
    importance = min(1.0, sum(w for pat, w in IMPORTANCE if pat.search(text)) / 1.5)
    # Title hits matter more than snippet hits.
    title_bonus = 0.15 if any(pat.search(story.title) for pat, _ in IMPORTANCE) else 0.0
    age_h = max(0.0, (now - story.published).total_seconds() / 3600) if story.published else 48.0
    recency = math.exp(-math.log(2) * age_h / 24)
    coverage = min(0.3, 0.1 * len(story.also_covered_by))
    official = 0.1 if story.official else 0.0
    return round(
        0.35 * importance + title_bonus + 0.25 * story.source_weight
        + 0.25 * recency + coverage + official,
        4,
    )


def rank(stories: list[Story], limit: int, now: datetime | None = None) -> list[Story]:
    now = now or datetime.now(UTC)
    candidates = dedupe([s for s in stories if is_relevant(s)])
    for s in candidates:
        s.score = score(s, now)
    candidates.sort(key=lambda s: (-s.score, -(s.published or now).timestamp(), s.title))
    picked: list[Story] = []
    counts: dict[str, int] = {}
    for story in candidates:
        if counts.get(story.source, 0) < PER_SOURCE_CAP:
            picked.append(story)
            counts[story.source] = counts.get(story.source, 0) + 1
    if len(picked) < limit:  # not enough variety: backfill with the best remaining
        picked += [s for s in candidates if s not in picked][: limit - len(picked)]
        picked.sort(key=lambda s: (-s.score, s.title))
    return picked[:limit]
