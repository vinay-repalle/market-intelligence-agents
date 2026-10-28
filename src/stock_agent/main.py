"""Entry point: prices + news -> Gemini interpretation -> Telegram (once per trading session)."""

from __future__ import annotations

import argparse
import html
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

import requests

from ai_market_agent import state, telegram
from ai_market_agent.errors import AgentError, CollectionError, ConfigError, GeminiError
from ai_market_agent.logutil import setup_logging

from . import analysis, market_data, news, report
from .config import StockConfig, load_stock_config

log = logging.getLogger("stock_agent")
STATE_PATH = Path(".stock_state/sent.json")


def run(
    config: StockConfig,
    *,
    dry_run: bool = False,
    force: bool = False,
    state_path: Path = STATE_PATH,
) -> int:
    """Send one report. Returns the number of Telegram messages (0 = skipped)."""
    now = datetime.now(UTC)
    client = None if dry_run else telegram.TelegramClient(
        config.telegram_bot_token, config.telegram_chat_id
    )
    indices, sectors, stocks = market_data.fetch_all(config.watchlist)

    sess = report.session_date(indices)
    key = f"stock-report:{sess or now.astimezone(market_data.IST).date().isoformat()}"
    if not dry_run and not force and key in state.load_sent(state_path):
        log.info("Report for %s was already sent; skipping (use --force to resend)", key)
        return 0

    news_ok = True
    try:
        headlines = news.collect(lookback_hours=config.lookback_hours, max_items=config.max_headlines, now=now)
    except CollectionError as exc:
        log.warning("News unavailable: %s", exc)
        headlines, news_ok = [], False

    block = report.data_block(indices, sectors, stocks)
    ai = None
    if block.startswith("(no market data") and not headlines:
        log.warning("No data and no headlines; skipping AI interpretation")
    else:
        try:
            ai = analysis.interpret(
                block, [h.title for h in headlines],
                api_key=config.gemini_api_key, model=config.gemini_model,
            )
        except GeminiError as exc:  # degrade: verified facts are still useful
            log.warning("AI interpretation unavailable: %s", exc)

    messages = report.format_report(indices, sectors, stocks, headlines, ai, now, news_ok)
    if client is None:
        print("\n\n----\n\n".join(messages))
        return len(messages)
    client.send_all(messages)
    state.save_sent([key], state_path, now)
    return len(messages)


def notify_failure(config: StockConfig | None, exc: BaseException) -> None:
    if config is None or not config.telegram_bot_token or not config.telegram_chat_id:
        return
    stage = getattr(exc, "stage", "agent")
    text = (
        f"❌ Stock report failed at stage '{html.escape(stage)}'.\n"
        f"{html.escape(str(exc)[:500])}\nCheck the GitHub Actions run logs."
    )
    try:
        telegram.TelegramClient(config.telegram_bot_token, config.telegram_chat_id).send(text)
    except Exception as notify_exc:  # noqa: BLE001 - never mask the original failure
        log.error("Could not send failure notification: %s", notify_exc)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Send the Indian market brief to Telegram.")
    parser.add_argument("--dry-run", action="store_true", help="Print the report, send nothing.")
    parser.add_argument("--force", action="store_true", help="Send even if already sent.")
    args = parser.parse_args(argv)
    setup_logging()
    config: StockConfig | None = None
    try:
        config = load_stock_config(require_telegram=not args.dry_run)
        setup_logging(config.secrets())
        sent = run(config, dry_run=args.dry_run, force=args.force)
        log.info("Done: %d message(s)", sent)
        return 0
    except ConfigError as exc:
        log.error("Configuration error: %s", exc)
        return 2
    except (AgentError, requests.RequestException) as exc:
        log.error("Run failed: %s", exc if isinstance(exc, AgentError) else type(exc).__name__)
        if not args.dry_run:
            notify_failure(config, exc)
        return 1
    except Exception as exc:
        log.exception("Unexpected error")
        if not args.dry_run:
            notify_failure(config, exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
