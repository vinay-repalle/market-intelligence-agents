from datetime import UTC, datetime, timedelta

import pytest

from ai_market_agent.models import Story

NOW = datetime(2026, 10, 28, 7, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never really wait during retry tests."""
    monkeypatch.setattr("ai_market_agent.retry.time.sleep", lambda s: None)


def make_story(title: str = "OpenAI launches new model", **kw: object) -> Story:
    defaults: dict[str, object] = {
        "title": title,
        "url": "https://example.com/" + title.lower().replace(" ", "-"),
        "source": "Example",
        "published": NOW - timedelta(hours=2),
        "snippet": "An AI announcement.",
        "source_weight": 0.8,
        "ai_only": True,
    }
    defaults.update(kw)
    return Story(**defaults)  # type: ignore[arg-type]
