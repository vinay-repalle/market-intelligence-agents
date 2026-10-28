import pytest

from ai_market_agent.config import load_config
from ai_market_agent.errors import ConfigError
from ai_market_agent.logutil import RedactingFilter

GOOD = {"GEMINI_API_KEY": "gk-test-123", "GEMINI_MODEL": "test-model", "TELEGRAM_BOT_TOKEN": "1:abc-test", "TELEGRAM_CHAT_ID": "-100"}


def test_loads_with_defaults() -> None:
    cfg = load_config(GOOD)
    assert cfg.max_stories == 10 and cfg.lookback_hours == 36
    assert cfg.gemini_model == "test-model"


def test_missing_vars_listed_with_instructions() -> None:
    with pytest.raises(ConfigError) as info:
        load_config({"GEMINI_API_KEY": "x" * 8, "GEMINI_MODEL": "m"})
    msg = str(info.value)
    assert "TELEGRAM_BOT_TOKEN" in msg and "TELEGRAM_CHAT_ID" in msg
    assert msg.split(".")[0] == "Missing required configuration: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID"
    assert ".env.example" in msg and "Secrets" in msg


@pytest.mark.parametrize("value", ["", "   ", "your-gemini-api-key", "<token>", "changeme"])
def test_blank_or_placeholder_counts_as_missing(value: str) -> None:
    with pytest.raises(ConfigError, match="GEMINI_API_KEY"):
        load_config({**GOOD, "GEMINI_API_KEY": value})


def test_dry_run_needs_only_gemini() -> None:
    assert load_config({"GEMINI_API_KEY": "gk-test", "GEMINI_MODEL": "m"}, require_telegram=False).telegram_chat_id == ""


@pytest.mark.parametrize("name,value", [("MAX_STORIES", "abc"), ("MAX_STORIES", "0"), ("LOOKBACK_HOURS", "9999")])
def test_bad_numeric_options(name: str, value: str) -> None:
    with pytest.raises(ConfigError, match=name):
        load_config({**GOOD, name: value})


def test_overrides() -> None:
    cfg = load_config({**GOOD, "MAX_STORIES": "5", "GEMINI_MODEL": "other-model"})
    assert cfg.max_stories == 5 and cfg.gemini_model == "other-model"


def test_redaction_filter() -> None:
    f = RedactingFilter(["supersecretvalue"])
    text = "k=supersecretvalue url https://api.telegram.org/bot123456:ABC-def/sendMessage?key=zzz"
    out = f.redact(text)
    assert "supersecretvalue" not in out and "ABC-def" not in out and "zzz" not in out


@pytest.mark.parametrize("value", [None, "", "  ", "your-gemini-model-name"])
def test_missing_model_explains_how_to_configure(value: str | None) -> None:
    env = {k: v for k, v in GOOD.items() if k != "GEMINI_MODEL"}
    if value is not None:
        env["GEMINI_MODEL"] = value
    with pytest.raises(ConfigError) as info:
        load_config(env)
    msg = str(info.value)
    assert msg.split(".")[0] == "Missing required configuration: GEMINI_MODEL"
    assert "AI Studio" in msg


def test_missing_model_also_fails_dry_run() -> None:
    with pytest.raises(ConfigError, match="GEMINI_MODEL"):
        load_config({"GEMINI_API_KEY": "gk-test"}, require_telegram=False)


def test_no_model_name_hardcoded_in_source() -> None:
    from pathlib import Path

    src = Path(__file__).resolve().parent.parent / "src"
    for path in src.rglob("*.py"):
        assert "gemini-1" not in path.read_text() and "gemini-2" not in path.read_text(), path
