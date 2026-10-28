"""Entry point: collect -> filter -> rank -> summarise -> deliver."""

from __future__ import annotations

import argparse
import html
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

import requests

from . import gemini, ranking, sources, state, telegram
from .config import Config, load_config
from .errors import AgentError, ConfigError
from .logutil import setup_logging

log = logging.getLogger("ai_market_agent")


def run(config: Config, *, dry_run: bool = False, state_path: Path = state.DEFAULT_PATH) -> int:
    """Execute one digest run. Returns the number of stories delivered."""
    now = datetime.now(UTC)
    client = None if dry_run else telegram.TelegramClient(
        config.telegram_bot_token, config.telegram_chat_id
    )

    stories, feeds_ok = sources.collect(lookback_hours=config.lookback_hours, now=now)
    log.info("Collected %d recent items from %d feeds", len(stories), feeds_ok)

    already_sent = state.load_sent(state_path)
    fresh = [s for s in stories if s.url not in already_sent]
    log.info("%d items remain after removing previously sent URLs", len(fresh))

    top = ranking.rank(fresh, config.max_stories, now)
    log.info("Selected %d stories", len(top))

    if not top:
        note = "ℹ️ AI digest: no new relevant AI stories were found in the last "
        note += f"{config.lookback_hours}h. Nothing was sent."
        log.warning(note)
        if client:
            client.send(html.escape(note))
        return 0

    summarised = gemini.summarize(top, api_key=config.gemini_api_key, model=config.gemini_model)
    messages = telegram.format_digest(summarised, now)

    if client is None:
        print("\n\n----\n\n".join(messages))
        return len(summarised)

    client.send_all(messages)
    state.save_sent([s.url for s in summarised], state_path, now)
    try:
        client.send(
            f"✅ Daily AI digest delivered: {len(summarised)} stories "
            f"from {feeds_ok} sources ({len(messages)} message(s))."
        )
    except AgentError as exc:  # digest itself is delivered; do not fail the run
        log.warning("Success notification failed: %s", exc)
    return len(summarised)


def notify_failure(config: Config | None, exc: BaseException) -> None:
    """Best-effort failure message to Telegram. Contains no secrets or stack traces."""
    if config is None or not config.telegram_bot_token or not config.telegram_chat_id:
        return
    stage = getattr(exc, "stage", "agent")
    text = (
        f"❌ AI digest failed at stage '{html.escape(stage)}'.\n"
        f"{html.escape(str(exc)[:500])}\n"
        "Check the GitHub Actions run logs for details."
    )
    try:
        telegram.TelegramClient(config.telegram_bot_token, config.telegram_chat_id).send(text)
    except Exception as notify_exc:  # noqa: BLE001 - never mask the original failure
        log.error("Could not send failure notification: %s", notify_exc)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Send the daily AI market digest to Telegram.")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Collect and summarise, print the digest, send nothing (needs only GEMINI_API_KEY).",
    )
    args = parser.parse_args(argv)
    setup_logging()

    config: Config | None = None
    try:
        config = load_config(require_telegram=not args.dry_run)
        setup_logging(config.secrets())
        delivered = run(config, dry_run=args.dry_run)
        log.info("Done: %d stories", delivered)
        return 0
    except ConfigError as exc:
        log.error("Configuration error: %s", exc)
        return 2
    except AgentError as exc:
        log.error("Run failed [%s]: %s", exc.stage, exc)
        if not args.dry_run:
            notify_failure(config, exc)
        return 1
    except requests.RequestException as exc:
        log.error("Unexpected network failure: %s", type(exc).__name__)
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
