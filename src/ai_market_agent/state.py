"""Tiny persisted state of already-sent URLs, to avoid repeats across days."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

log = logging.getLogger(__name__)
DEFAULT_PATH = Path(".agent_state/sent.json")
RETENTION_DAYS = 14


def load_sent(path: Path = DEFAULT_PATH) -> dict[str, str]:
    """Return {url: iso timestamp}. A missing or corrupt file just means no history."""
    try:
        data = json.loads(path.read_text())
        return {str(k): str(v) for k, v in data.items()}
    except FileNotFoundError:
        return {}
    except (ValueError, AttributeError, OSError):
        log.warning("State file unreadable; starting with empty history")
        return {}


def save_sent(
    urls: list[str], path: Path = DEFAULT_PATH, now: datetime | None = None
) -> None:
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(days=RETENTION_DAYS)
    history: dict[str, str] = {}
    for url, ts in load_sent(path).items():
        when = _parse(ts)
        if when is not None and when >= cutoff:
            history[url] = ts
    history.update({u: now.isoformat() for u in urls})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(history, indent=1, sort_keys=True))


def _parse(ts: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(ts)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)

