"""Stock agent configuration. Reuses the Gemini and Telegram bot settings of the news agent."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from ai_market_agent.config import _int, _is_missing
from ai_market_agent.errors import ConfigError

REQUIRED = ("GEMINI_API_KEY", "GEMINI_MODEL", "TELEGRAM_BOT_TOKEN", "TELEGRAM_STOCK_CHAT_ID")
GEMINI_VARS = ("GEMINI_API_KEY", "GEMINI_MODEL")
MAX_WATCHLIST = 15


@dataclass(frozen=True)
class StockConfig:
    gemini_api_key: str
    gemini_model: str
    telegram_bot_token: str
    telegram_chat_id: str
    watchlist: tuple[str, ...] = ()
    lookback_hours: int = 30
    max_headlines: int = 8

    def secrets(self) -> list[str]:
        return [self.gemini_api_key, self.telegram_bot_token]


def parse_watchlist(raw: str) -> tuple[str, ...]:
    """'RELIANCE, tcs.NS' -> ('RELIANCE.NS', 'TCS.NS'). Duplicates and blanks are dropped."""
    seen: dict[str, None] = {}
    for part in raw.replace(";", ",").split(","):
        sym = part.strip().upper()
        if not sym:
            continue
        if not sym.endswith((".NS", ".BO")):
            sym += ".NS"
        if not all(c.isalnum() or c in "&.-_" for c in sym):
            raise ConfigError(f"STOCK_WATCHLIST has an invalid symbol: {part.strip()!r}.")
        seen.setdefault(sym, None)
    if len(seen) > MAX_WATCHLIST:
        raise ConfigError(f"STOCK_WATCHLIST may hold at most {MAX_WATCHLIST} symbols.")
    return tuple(seen)


def load_stock_config(
    env: Mapping[str, str] | None = None,
    *,
    require_telegram: bool = True,
    use_dotenv: bool = True,
) -> StockConfig:
    if env is None:
        if use_dotenv:
            load_dotenv(Path.cwd() / ".env")
        env = os.environ
    required = REQUIRED if require_telegram else GEMINI_VARS
    missing = [n for n in required if _is_missing(env.get(n))]
    if missing:
        raise ConfigError(
            "Missing required configuration: " + ", ".join(missing) + ". Locally: copy "
            ".env.example to .env and fill in real values. In GitHub Actions: add the "
            "matching secrets/variables (see README.md)."
        )
    return StockConfig(
        gemini_api_key=env.get("GEMINI_API_KEY", "").strip(),
        gemini_model=env.get("GEMINI_MODEL", "").strip(),
        telegram_bot_token=env.get("TELEGRAM_BOT_TOKEN", "").strip(),
        telegram_chat_id=env.get("TELEGRAM_STOCK_CHAT_ID", "").strip(),
        watchlist=parse_watchlist(env.get("STOCK_WATCHLIST", "")),
        lookback_hours=_int(env, "STOCK_LOOKBACK_HOURS", 30, 1, 96),
        max_headlines=_int(env, "STOCK_MAX_HEADLINES", 8, 1, 15),
    )
