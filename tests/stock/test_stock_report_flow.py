import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
import responses

from ai_market_agent import gemini, state, telegram
from ai_market_agent.errors import CollectionError, GeminiError
from ai_market_agent.models import Story
from stock_agent import analysis, market_data, news, report
from stock_agent import main as stock_main
from stock_agent.config import StockConfig
from stock_agent.market_data import Quote

NOW = datetime(2026, 10, 28, 1, 30, tzinfo=UTC)
CFG = StockConfig("k" * 10, "m", "t" * 10, "-100222", watchlist=("TCS.NS",))
SENT_URL = telegram.API.format(token="t" * 10, method="sendMessage")


def q(name: str, close: float | None, prev: float | None, sess: date | None = date(2026, 10, 27)) -> Quote:
    return Quote(name, name, close, prev, sess, "" if close else "no data")


def story(title: str = "Nifty ends higher on FII buying", **kw: object) -> Story:
    d: dict[str, object] = {"title": title, "url": "https://ex.test/" + title[:5], "source": "ET",
                            "published": NOW, "source_weight": 0.9}
    d.update(kw)
    return Story(**d)  # type: ignore[arg-type]


def gemini_ok(text: str) -> dict[str, object]:
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


# --- report formatting -------------------------------------------------------

def test_report_separates_verified_from_ai_and_labels_unavailable() -> None:
    idx = [q("NIFTY 50", 22698.85, 22687.65), q("SENSEX", None, None)]
    ai = analysis.Interpretation(["Gains may reflect FII buying."], {1: "Flows can lift sentiment."})
    text = "\n".join(report.format_report(idx, [], [], [story()], ai, NOW))
    assert text.index("Verified data") < text.index("Headlines") < text.index("AI interpretation")
    assert "22,698.85 (+0.05%)" in text and "SENSEX: data unavailable" in text
    assert "unverified" in text and "not investment advice" in text
    assert 'href="https://ex.test/Nifty"' in text


def test_report_without_ai_or_news_says_so_and_invents_nothing() -> None:
    text = "\n".join(report.format_report([q("NIFTY 50", None, None, None)], [], [], [], None, NOW, news_ok=False))
    assert "news unavailable" in text and "AI interpretation</b>\nunavailable" in text
    assert "unknown" in text


def test_report_html_escapes_headlines_and_fits_limit() -> None:
    hs = [story(f"<b>x</b> & Nifty {i} " + "t" * 300, url=f"https://ex.test/{i}") for i in range(8)]
    msgs = report.format_report([q("NIFTY 50", 1.0, 1.0)], [], [], hs, None, NOW)
    assert all(len(m) <= telegram.LIMIT for m in msgs)
    assert "<b>x</b>" not in "".join(msgs)


# --- AI guardrails -----------------------------------------------------------

def test_ai_text_with_unsupported_numbers_is_dropped() -> None:
    data = "NIFTY 50: close 22,698.85, change +0.05%"
    allowed = analysis._numbers(data)
    raw = json.dumps({"market_view": ["Nifty rose 0.05%.", "Nifty will hit 25000 soon."],
                      "headline_notes": [{"id": 1, "note": "ok"}, {"id": 9, "note": "bad id"}]})
    out = analysis.parse(raw, allowed, {1})
    assert out.market_view == ["Nifty rose 0.05%."] and out.notes == {1: "ok"}


def test_ai_malformed_json_is_transient() -> None:
    from ai_market_agent.errors import TransientError
    with pytest.raises(TransientError):
        analysis.parse("nope", set(), set())


@responses.activate
def test_interpret_quota_error_surfaces_as_gemini_error() -> None:
    err = {"error": {"status": "RESOURCE_EXHAUSTED", "message": "per day"}}
    responses.add(responses.POST, gemini.ENDPOINT.format(model="m"), status=429, json=err)
    with pytest.raises(GeminiError):
        analysis.interpret("d", ["h"], api_key="k" * 10, model="m")


# --- news --------------------------------------------------------------------

def test_news_collect_filters_dedupes_and_tolerates_feed_failure() -> None:
    rss = b"""<rss><channel><item><title>Sensex jumps as RBI holds repo rate</title><link>https://a.test/1?utm_x=1</link>
    <pubDate>Tue, 27 Oct 2026 20:00:00 GMT</pubDate></item>
    <item><title>Sensex jumps as RBI holds repo rate</title><link>https://b.test/2</link>
    <pubDate>Tue, 27 Oct 2026 21:00:00 GMT</pubDate></item>
    <item><title>Celebrity wedding photos</title><link>https://a.test/3</link>
    <pubDate>Tue, 27 Oct 2026 21:00:00 GMT</pubDate></item></channel></rss>"""
    feeds = news.FEEDS[:2]

    def fetcher(_s: object, feed: news.Feed) -> bytes:
        if feed is feeds[1]:
            raise CollectionError("down")
        return rss

    out = news.collect(lookback_hours=30, max_items=5, now=NOW, feeds=feeds, fetcher=fetcher)  # type: ignore[arg-type]
    assert [s.title for s in out] == ["Sensex jumps as RBI holds repo rate"]


def test_news_all_feeds_failing_raises() -> None:
    def fetcher(_s: object, _f: news.Feed) -> bytes:
        raise CollectionError("down")

    with pytest.raises(CollectionError):
        news.collect(lookback_hours=30, max_items=5, now=NOW, fetcher=fetcher)  # type: ignore[arg-type]


# --- end-to-end run with duplicate prevention --------------------------------

@pytest.fixture
def patched(monkeypatch: pytest.MonkeyPatch) -> None:
    idx = [q("NIFTY 50", 100.0, 99.0), q("SENSEX", 200.0, 198.0), q("BANK NIFTY", 300.0, 303.0)]
    monkeypatch.setattr(market_data, "fetch_all", lambda wl: (idx, [], [q("TCS", 10.0, 9.0)]))
    monkeypatch.setattr(news, "collect", lambda **kw: [story()])
    monkeypatch.setattr(analysis, "interpret", lambda *a, **k: analysis.Interpretation(["Calm session."]))


@responses.activate
def test_run_sends_once_then_skips_duplicate(patched: None, tmp_path: Path) -> None:
    responses.add(responses.POST, SENT_URL, json={"ok": True})
    sp = tmp_path / "s.json"
    assert stock_main.run(CFG, state_path=sp) >= 1
    sent = len(responses.calls)
    assert stock_main.run(CFG, state_path=sp) == 0
    assert len(responses.calls) == sent
    assert stock_main.run(CFG, state_path=sp, force=True) >= 1
    assert "stock-report:2026-10-27" in state.load_sent(sp)
    assert json.loads(responses.calls[0].request.body)["chat_id"] == "-100222"


@responses.activate
def test_telegram_failure_propagates_and_is_not_marked_sent(patched: None, tmp_path: Path) -> None:
    from ai_market_agent.errors import TelegramError
    responses.add(responses.POST, SENT_URL, status=403, json={"description": "Forbidden"})
    sp = tmp_path / "s.json"
    with pytest.raises(TelegramError):
        stock_main.run(CFG, state_path=sp)
    assert state.load_sent(sp) == {}


def test_dry_run_prints_and_sends_nothing(patched: None, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert stock_main.run(CFG, dry_run=True, state_path=tmp_path / "s.json") >= 1
    assert "Verified data" in capsys.readouterr().out
    assert not (tmp_path / "s.json").exists()


@responses.activate
def test_gemini_failure_still_sends_verified_report(
    patched: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def boom(*a: object, **k: object) -> None:
        raise GeminiError("quota")

    monkeypatch.setattr(analysis, "interpret", boom)
    responses.add(responses.POST, SENT_URL, json={"ok": True})
    assert stock_main.run(CFG, state_path=tmp_path / "s.json") >= 1
    assert "unavailable today" in json.loads(responses.calls[0].request.body)["text"]


@responses.activate
def test_news_failure_still_sends_report(patched: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def down(**k: object) -> list[Story]:
        raise CollectionError("down")

    monkeypatch.setattr(news, "collect", down)
    responses.add(responses.POST, SENT_URL, json={"ok": True})
    assert stock_main.run(CFG, state_path=tmp_path / "s.json") >= 1
    assert "news unavailable" in json.loads(responses.calls[0].request.body)["text"]


@responses.activate
def test_main_failure_notifies_stock_chat_and_returns_1(
    patched: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    for k, v in {"GEMINI_API_KEY": "k" * 10, "GEMINI_MODEL": "m", "TELEGRAM_BOT_TOKEN": "t" * 10,
                 "TELEGRAM_STOCK_CHAT_ID": "-100222"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.chdir(Path(__file__).parent)  # no .env here
    monkeypatch.setattr(stock_main, "run", lambda *a, **k: (_ for _ in ()).throw(CollectionError("boom")))
    responses.add(responses.POST, SENT_URL, json={"ok": True})
    assert stock_main.main([]) == 1
    assert "Stock report failed" in json.loads(responses.calls[0].request.body)["text"]


def test_main_missing_config_exit_2(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in ("GEMINI_API_KEY", "GEMINI_MODEL", "TELEGRAM_BOT_TOKEN", "TELEGRAM_STOCK_CHAT_ID"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.chdir(Path(__file__).parent)
    assert stock_main.main([]) == 2
