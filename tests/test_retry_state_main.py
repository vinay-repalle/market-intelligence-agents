import json
from datetime import timedelta
from pathlib import Path

import pytest
import responses

from ai_market_agent import main as agent_main
from ai_market_agent import state, telegram
from ai_market_agent.config import Config
from ai_market_agent.errors import GeminiAuthError, PermanentError, TransientError
from ai_market_agent.retry import retry_call
from conftest import NOW, make_story

CFG = Config("gk-test-key", "test-model", "1:tok-test", "-100")
TG_URL = telegram.API.format(token="1:tok-test", method="sendMessage")


# --- retry ---------------------------------------------------------------
def test_retry_exponential_backoff_and_success() -> None:
    delays: list[float] = []
    calls = {"n": 0}

    def flaky() -> str:
        calls["n"] += 1
        if calls["n"] < 4:
            raise TransientError("temp")
        return "ok"

    assert retry_call(flaky, what="t", attempts=4, base_delay=1, sleep=delays.append) == "ok"
    assert len(delays) == 3 and delays[0] < delays[1] < delays[2]


def test_retry_permanent_not_retried() -> None:
    calls = {"n": 0}

    def bad() -> None:
        calls["n"] += 1
        raise PermanentError("no")

    with pytest.raises(PermanentError):
        retry_call(bad, what="t", sleep=lambda s: None)
    assert calls["n"] == 1


def test_retry_after_respected() -> None:
    delays: list[float] = []
    seq = iter([TransientError("x", retry_after=20), None])

    def f() -> None:
        e = next(seq)
        if e:
            raise e

    retry_call(f, what="t", base_delay=1, sleep=delays.append)
    assert delays[0] >= 20


# --- state ---------------------------------------------------------------
def test_state_roundtrip_and_prune(tmp_path: Path) -> None:
    p = tmp_path / "s" / "sent.json"
    assert state.load_sent(p) == {}
    state.save_sent(["https://a"], p, NOW - timedelta(days=30))
    state.save_sent(["https://b"], p, NOW)
    assert set(state.load_sent(p)) == {"https://b"}  # a expired


def test_corrupt_state_is_ignored(tmp_path: Path) -> None:
    p = tmp_path / "sent.json"
    p.write_text("{not json")
    assert state.load_sent(p) == {}


# --- orchestration -------------------------------------------------------
def patch_pipeline(mp: pytest.MonkeyPatch, stories: list) -> None:  # type: ignore[type-arg]
    mp.setattr(agent_main.sources, "collect", lambda **kw: (stories, 3))

    def fake_summarize(items: list, **kw: object) -> list:  # type: ignore[type-arg]
        for s in items:
            s.summary = "Summary."
        return items

    mp.setattr(agent_main.gemini, "summarize", fake_summarize)


@responses.activate
def test_run_sends_digest_then_success_and_records_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    patch_pipeline(monkeypatch, [make_story(f"Vendor{i} launches thing{i}") for i in range(4)])
    responses.add(responses.POST, TG_URL, json={"ok": True})
    n = agent_main.run(CFG, state_path=tmp_path / "s.json")
    assert n == 4
    texts = [json.loads(c.request.body)["text"] for c in responses.calls]
    assert "Top 4 updates" in texts[0] and "delivered" in texts[-1]
    assert len(state.load_sent(tmp_path / "s.json")) == 4


@responses.activate
def test_run_skips_previously_sent_urls(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    s = make_story("Vendor launches thing")
    state.save_sent([s.url], tmp_path / "s.json")
    patch_pipeline(monkeypatch, [s])
    responses.add(responses.POST, TG_URL, json={"ok": True})
    assert agent_main.run(CFG, state_path=tmp_path / "s.json") == 0
    assert "no new relevant" in json.loads(responses.calls[0].request.body)["text"]


@responses.activate
def test_delivery_failure_does_not_record_state_or_claim_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    patch_pipeline(monkeypatch, [make_story("Vendor launches thing")])
    responses.add(responses.POST, TG_URL, status=403, json={"description": "bot was kicked"})
    with pytest.raises(PermanentError):
        agent_main.run(CFG, state_path=tmp_path / "s.json")
    assert not (tmp_path / "s.json").exists()
    assert all("delivered" not in json.loads(c.request.body)["text"] for c in responses.calls)


def test_dry_run_prints_and_sends_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    patch_pipeline(monkeypatch, [make_story("Vendor launches thing")])
    with responses.RequestsMock() as rsps:  # any HTTP call would raise
        assert agent_main.run(CFG, dry_run=True, state_path=tmp_path / "s.json") == 1
        assert len(rsps.calls) == 0
    assert "Vendor launches thing" in capsys.readouterr().out


@responses.activate
def test_main_gemini_failure_exit_1_and_notifies(monkeypatch: pytest.MonkeyPatch) -> None:
    for k, v in {"GEMINI_API_KEY": "gk-test-key", "TELEGRAM_BOT_TOKEN": "1:tok-test", "TELEGRAM_CHAT_ID": "-100"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(agent_main, "load_config", lambda **kw: CFG)
    monkeypatch.setattr(agent_main.sources, "collect", lambda **kw: ([make_story("Vendor launches x")], 1))

    def boom(*a: object, **k: object) -> None:
        raise GeminiAuthError("Gemini rejected the API key")

    monkeypatch.setattr(agent_main.gemini, "summarize", boom)
    responses.add(responses.POST, TG_URL, json={"ok": True})
    assert agent_main.main([]) == 1
    sent = json.loads(responses.calls[0].request.body)["text"]
    assert "failed at stage 'gemini'" in sent and "gk-test-key" not in sent
    assert len(responses.calls) == 1  # no digest, no success message


def test_main_missing_config_exit_2(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in ("GEMINI_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(agent_main, "load_config", lambda **kw: agent_main.ConfigError("missing"))  # noqa: E501

    def raiser(**kw: object) -> None:
        raise agent_main.ConfigError("missing")

    monkeypatch.setattr(agent_main, "load_config", raiser)
    assert agent_main.main([]) == 2


@responses.activate
def test_failure_notification_failure_is_swallowed() -> None:
    responses.add(responses.POST, TG_URL, status=401, json={"description": "bad"})
    agent_main.notify_failure(CFG, PermanentError("x"))  # must not raise


def test_notify_failure_without_config_is_noop() -> None:
    agent_main.notify_failure(None, PermanentError("x"))
