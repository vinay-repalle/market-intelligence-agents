import pytest
import requests
import responses

from ai_market_agent import telegram
from ai_market_agent.errors import TelegramError, TransientError
from conftest import NOW, make_story

TOKEN = "123456:SECRET-token_abc"
URL = telegram.API.format(token=TOKEN, method="sendMessage")


def client() -> telegram.TelegramClient:
    return telegram.TelegramClient(TOKEN, "-100123")


def test_format_story_escapes_html_and_links() -> None:
    s = make_story("A <b>&</b> B", url="https://x.test/a?x=1&y=2", summary="Uses <script> & more")
    text = telegram.format_story(1, s)
    assert "<b>1. A &lt;b&gt;&amp;&lt;/b&gt; B</b>" in text
    assert '<a href="https://x.test/a?x=1&amp;y=2">Example · 28 Oct</a>' in text
    assert "• Uses &lt;script&gt; &amp; more" in text and "<script>" not in text


def test_format_story_shows_at_most_three_bullets() -> None:
    text = telegram.format_story(1, make_story(summary="a\nb\nc\nd"))
    assert text.count("•") == 3


def test_format_story_without_date() -> None:
    s = make_story(published=None)
    assert "·" not in telegram.format_story(1, s)


def test_split_respects_limit_and_keeps_blocks_whole() -> None:
    blocks = [f"<b>{i}</b> " + "x" * 900 for i in range(10)]
    msgs = telegram.split_messages("HEAD", blocks, limit=2000)
    assert len(msgs) > 1
    assert all(len(m) <= 2000 for m in msgs)
    joined = "\n\n".join(msgs)
    assert all(b in joined for b in blocks)
    assert msgs[0].startswith("HEAD")


def test_split_truncates_oversized_block() -> None:
    msgs = telegram.split_messages("H", ["y" * 10000], limit=1000)
    assert all(len(m) <= 1000 for m in msgs)


def test_digest_of_ten_long_stories_fits_telegram_limit() -> None:
    stories = [make_story(f"T{i} " + "t" * 190, summary="s" * 600) for i in range(10)]
    msgs = telegram.format_digest(stories, NOW)
    assert all(len(m) <= telegram.LIMIT for m in msgs)
    assert "Top 10 updates" in msgs[0]
    assert sum(m.count("<a href") for m in msgs) == 10


@responses.activate
def test_send_posts_html_payload() -> None:
    responses.add(responses.POST, URL, json={"ok": True})
    client().send("hi")
    body = responses.calls[0].request.body
    assert b'"parse_mode": "HTML"' in body and b"-100123" in body


@responses.activate
def test_send_retries_429_honouring_retry_after() -> None:
    responses.add(responses.POST, URL, status=429, json={"parameters": {"retry_after": 1}})
    responses.add(responses.POST, URL, json={"ok": True})
    client().send("hi")
    assert len(responses.calls) == 2


@responses.activate
@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_permanent_errors_not_retried(status: int) -> None:
    responses.add(responses.POST, URL, status=status, json={"description": "nope"})
    with pytest.raises(TelegramError, match=f"HTTP {status}"):
        client().send("hi")
    assert len(responses.calls) == 1


@responses.activate
def test_server_errors_exhaust_retries() -> None:
    responses.add(responses.POST, URL, status=502)
    with pytest.raises(TransientError):
        client().send("hi")
    assert len(responses.calls) == 4


@responses.activate
def test_network_error_message_never_contains_token() -> None:
    responses.add(responses.POST, URL, body=requests.ConnectionError(f"failed for {URL}"))
    with pytest.raises(TransientError) as info:
        client().send("hi")
    assert TOKEN not in str(info.value)
