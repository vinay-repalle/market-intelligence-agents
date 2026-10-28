import pytest
import requests
import responses

from ai_market_agent.errors import ConfigError
from stock_agent import market_data
from stock_agent.config import load_stock_config, parse_watchlist

URL = market_data.URL.format(symbol="^NSEI")


def chart(closes: list[float | None], stamps: list[int] | None = None) -> dict[str, object]:
    stamps = stamps or [1793072700 + i * 86400 for i in range(len(closes))]
    return {"chart": {"result": [{"timestamp": stamps, "indicators": {"quote": [{"close": closes}]}}]}}


def test_parse_uses_last_two_valid_closes() -> None:
    q = market_data.parse_chart(chart([100.0, None, 110.0, 121.0]), "^NSEI", "NIFTY 50")
    assert q.available and q.close == 121.0 and q.prev_close == 110.0
    assert q.change_pct == pytest.approx(10.0)
    assert q.session is not None


@pytest.mark.parametrize("payload", [{}, {"chart": {"result": None}}, chart([5.0]), chart([None, None])])
def test_parse_missing_data_is_unavailable_not_invented(payload: object) -> None:
    q = market_data.parse_chart(payload, "X", "X")
    assert not q.available and q.change_pct is None and q.error


@responses.activate
def test_fetch_quote_success() -> None:
    responses.add(responses.GET, URL, json=chart([100.0, 101.0]))
    assert market_data.fetch_quote(requests.Session(), "^NSEI", "NIFTY 50").available


@responses.activate
def test_fetch_quote_rate_limit_retries_then_reports_unavailable() -> None:
    responses.add(responses.GET, URL, status=429)
    q = market_data.fetch_quote(requests.Session(), "^NSEI", "NIFTY 50")
    assert not q.available and "rate limit" in q.error
    assert len(responses.calls) == 3


@responses.activate
def test_fetch_quote_recovers_after_server_error() -> None:
    responses.add(responses.GET, URL, status=503)
    responses.add(responses.GET, URL, json=chart([100.0, 99.0]))
    assert market_data.fetch_quote(requests.Session(), "^NSEI", "N").available


@responses.activate
def test_fetch_quote_404_and_bad_json_are_unavailable() -> None:
    responses.add(responses.GET, URL, status=404)
    assert not market_data.fetch_quote(requests.Session(), "^NSEI", "N").available
    responses.replace(responses.GET, URL, body="<html>")
    assert not market_data.fetch_quote(requests.Session(), "^NSEI", "N").available


@responses.activate
def test_fetch_quote_timeout_is_unavailable() -> None:
    responses.add(responses.GET, URL, body=requests.Timeout("slow"))
    assert not market_data.fetch_quote(requests.Session(), "^NSEI", "N").available


def test_watchlist_parsing() -> None:
    assert parse_watchlist("reliance, TCS.NS;tcs.ns, ,500325.BO") == ("RELIANCE.NS", "TCS.NS", "500325.BO")
    assert parse_watchlist("") == ()
    with pytest.raises(ConfigError):
        parse_watchlist("BAD SYMBOL!")
    with pytest.raises(ConfigError):
        parse_watchlist(",".join(f"S{i}" for i in range(16)))


def test_config_requires_stock_chat_id_not_news_chat_id() -> None:
    env = {"GEMINI_API_KEY": "k" * 10, "GEMINI_MODEL": "m", "TELEGRAM_BOT_TOKEN": "t" * 10,
           "TELEGRAM_CHAT_ID": "-100111"}
    with pytest.raises(ConfigError, match="TELEGRAM_STOCK_CHAT_ID"):
        load_stock_config(env, use_dotenv=False)
    cfg = load_stock_config({**env, "TELEGRAM_STOCK_CHAT_ID": "-100222", "STOCK_WATCHLIST": "infy"},
                            use_dotenv=False)
    assert cfg.telegram_chat_id == "-100222" and cfg.watchlist == ("INFY.NS",)


def test_dry_run_config_needs_only_gemini() -> None:
    cfg = load_stock_config({"GEMINI_API_KEY": "k" * 10, "GEMINI_MODEL": "m"},
                            require_telegram=False, use_dotenv=False)
    assert cfg.gemini_model == "m"
