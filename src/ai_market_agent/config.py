"""Configuration loaded from environment variables (optionally via a local .env)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from .errors import ConfigError

REQUIRED = ("GEMINI_API_KEY", "GEMINI_MODEL", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")
GEMINI_VARS = ("GEMINI_API_KEY", "GEMINI_MODEL")
PLACEHOLDER_PREFIXES = ("your-", "changeme", "<")


@dataclass(frozen=True)
class Config:
    gemini_api_key: str
    gemini_model: str
    telegram_bot_token: str
    telegram_chat_id: str
    max_stories: int = 10
    lookback_hours: int = 36

    def secrets(self) -> list[str]:
        return [self.gemini_api_key, self.telegram_bot_token]


def _is_missing(value: str | None) -> bool:
    if value is None or not value.strip():
        return True
    return value.strip().lower().startswith(PLACEHOLDER_PREFIXES)


def _int(env: Mapping[str, str], name: str, default: int, low: int, high: int) -> int:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be an integer, got {raw!r}.") from None
    if not low <= value <= high:
        raise ConfigError(f"{name} must be between {low} and {high}, got {value}.")
    return value


def load_config(
    env: Mapping[str, str] | None = None,
    *,
    require_telegram: bool = True,
    use_dotenv: bool = True,
) -> Config:
    """Build a Config, raising ConfigError with setup instructions if incomplete."""
    if env is None:
        if use_dotenv:
            # Only ./.env: never search parent directories (could pick up an unrelated .env).
            # Real environment variables take precedence over the file.
            load_dotenv(Path.cwd() / ".env")
        env = os.environ

    required = REQUIRED if require_telegram else GEMINI_VARS
    missing = [name for name in required if _is_missing(env.get(name))]
    if missing:
        raise ConfigError(
            "Missing required configuration: "
            + ", ".join(missing)
            + ". Locally: copy .env.example to .env and fill in real values. "
            "In GitHub Actions: add secrets GEMINI_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID and a "
            "repository variable GEMINI_MODEL (Settings > Secrets and variables > Actions). "
            "GEMINI_MODEL must name a Gemini model available to your key (see Google AI Studio). "
            "See README.md."
        )

    return Config(
        gemini_api_key=env.get("GEMINI_API_KEY", "").strip(),
        telegram_bot_token=env.get("TELEGRAM_BOT_TOKEN", "").strip(),
        telegram_chat_id=env.get("TELEGRAM_CHAT_ID", "").strip(),
        gemini_model=env.get("GEMINI_MODEL", "").strip(),
        max_stories=_int(env, "MAX_STORIES", 10, 1, 25),
        lookback_hours=_int(env, "LOOKBACK_HOURS", 36, 1, 168),
    )
