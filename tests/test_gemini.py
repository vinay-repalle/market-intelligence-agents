import json

import pytest
import requests
import responses

from ai_market_agent import gemini
from ai_market_agent.errors import GeminiAuthError, GeminiError, GeminiQuotaError, TransientError
from conftest import make_story

URL = gemini.ENDPOINT.format(model="m")


def ok_body(items: list[dict[str, object]] | str) -> dict[str, object]:
    text = items if isinstance(items, str) else json.dumps(items)
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


def run(n: int = 2) -> list:  # type: ignore[type-arg]
    stories = [make_story(f"Story {i}") for i in range(n)]
    return gemini.summarize(stories, api_key="k" * 10, model="m")


def test_parse_handles_fences_and_filters_bad_rows() -> None:
    text = '```json\n[{"id":1,"summary":" A  b "},{"id":9,"summary":"x"},{"id":2,"summary":""},"junk"]\n```'
    assert gemini.parse_summaries(text, {1, 2}) == {1: "A b"}


@pytest.mark.parametrize("text", ["not json", '{"id":1}', "[]", '[{"id":5,"summary":"x"}]'])
def test_parse_rejects_unusable(text: str) -> None:
    with pytest.raises(TransientError):
        gemini.parse_summaries(text, {1})


def test_parse_truncates_long_summary() -> None:
    out = gemini.parse_summaries(json.dumps([{"id": 1, "summary": "w " * 500}]), {1})
    assert len(out[1]) <= gemini.MAX_SUMMARY_CHARS


def test_prompt_contains_items_and_rules() -> None:
    p = gemini.build_prompt([make_story("Alpha")])
    assert "[1] Title: Alpha" in p and "ONLY facts" in p


@responses.activate
def test_success_sends_key_in_header_not_url() -> None:
    responses.add(responses.POST, URL, json=ok_body([{"id": 1, "summary": "S1"}, {"id": 2, "summary": "S2"}]))
    out = run()
    assert [s.summary for s in out] == ["S1", "S2"]
    call = responses.calls[0].request
    assert call.headers["x-goog-api-key"] == "k" * 10
    assert "key=" not in call.url


@responses.activate
def test_missing_summary_drops_story_not_invents() -> None:
    responses.add(responses.POST, URL, json=ok_body([{"id": 2, "summary": "only two"}]))
    out = run()
    assert len(out) == 1 and out[0].title == "Story 1" and out[0].summary == "only two"


@responses.activate
def test_invalid_key_is_permanent_no_retry() -> None:
    err = {"error": {"code": 400, "status": "INVALID_ARGUMENT", "message": "API key not valid", "details": [{"reason": "API_KEY_INVALID"}]}}
    responses.add(responses.POST, URL, status=400, json=err)
    with pytest.raises(GeminiAuthError, match="aistudio"):
        run()
    assert len(responses.calls) == 1


@responses.activate
def test_403_is_auth_error() -> None:
    responses.add(responses.POST, URL, status=403, json={"error": {"status": "PERMISSION_DENIED"}})
    with pytest.raises(GeminiAuthError):
        run()
    assert len(responses.calls) == 1


@responses.activate
def test_daily_quota_exhausted_not_retried() -> None:
    err = {"error": {"status": "RESOURCE_EXHAUSTED", "details": [{"violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}]}}
    responses.add(responses.POST, URL, status=429, json=err)
    with pytest.raises(GeminiQuotaError, match="daily"):
        run()
    assert len(responses.calls) == 1


@responses.activate
def test_rate_limit_retries_then_succeeds() -> None:
    responses.add(responses.POST, URL, status=429, json={"error": {"status": "RESOURCE_EXHAUSTED"}})
    responses.add(responses.POST, URL, json=ok_body([{"id": 1, "summary": "a"}, {"id": 2, "summary": "b"}]))
    assert len(run()) == 2
    assert len(responses.calls) == 2


@responses.activate
def test_rate_limit_persisting_fails_after_retries() -> None:
    responses.add(responses.POST, URL, status=429, json={"error": {"status": "RESOURCE_EXHAUSTED"}})
    with pytest.raises(GeminiError, match="unavailable after retries"):
        run()
    assert len(responses.calls) == 4


@responses.activate
def test_server_error_then_success() -> None:
    responses.add(responses.POST, URL, status=503)
    responses.add(responses.POST, URL, json=ok_body([{"id": 1, "summary": "a"}]))
    assert len(run(1)) == 1


@responses.activate
def test_timeout_is_retried() -> None:
    responses.add(responses.POST, URL, body=requests.Timeout("slow"))
    responses.add(responses.POST, URL, json=ok_body([{"id": 1, "summary": "a"}]))
    assert len(run(1)) == 1


@responses.activate
def test_unknown_model_404() -> None:
    responses.add(responses.POST, URL, status=404, json={"error": {"status": "NOT_FOUND"}})
    with pytest.raises(GeminiError, match="GEMINI_MODEL"):
        run()


@responses.activate
def test_blocked_prompt_fails_cleanly() -> None:
    responses.add(responses.POST, URL, json={"promptFeedback": {"blockReason": "SAFETY"}})
    with pytest.raises(GeminiError, match="SAFETY"):
        run()


def test_empty_input_makes_no_call() -> None:
    assert gemini.summarize([], api_key="k", model="m") == []
