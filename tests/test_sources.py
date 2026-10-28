from datetime import timedelta

import pytest
import requests
import responses

from ai_market_agent import sources
from ai_market_agent.errors import CollectionError
from ai_market_agent.sources import Feed
from conftest import NOW

FEED = Feed("Test", "https://feed.test/rss", 0.9, True, True)

RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>
<item><title>  Big &amp; Bold   Launch </title>
<link>https://x.test/a/?utm_source=tw&amp;id=7#frag</link>
<pubDate>Wed, 28 Oct 2026 05:00:00 GMT</pubDate>
<description>&lt;p&gt;Hello &lt;b&gt;world&lt;/b&gt;&lt;/p&gt;</description></item>
<item><title>No link here</title></item>
<item><title>Old story</title><link>https://x.test/old</link>
<pubDate>Mon, 01 Jan 2024 00:00:00 GMT</pubDate></item>
<item><title>Undated</title><link>https://x.test/undated</link></item>
<item><title>Bad scheme</title><link>javascript:alert(1)</link></item>
</channel></rss>"""


def test_parse_normalises_entries() -> None:
    stories = sources.parse_feed(RSS, FEED)
    first = stories[0]
    assert first.title == "Big & Bold Launch"
    assert first.url == "https://x.test/a?id=7"  # tracking param + fragment + slash removed
    assert first.snippet == "Hello world"
    assert first.published is not None and first.published.hour == 5
    assert first.source == "Test" and first.official
    assert {s.title for s in stories} == {"Big & Bold Launch", "Old story", "Undated"}


def test_canonical_url_equates_variants() -> None:
    a = sources.canonical_url("HTTPS://X.test/path/?utm_medium=a&b=1")
    assert a == sources.canonical_url("https://x.test/path?b=1#top")


def test_parse_garbage_raises() -> None:
    with pytest.raises(CollectionError):
        sources.parse_feed(b"<<not xml at all", FEED)


@responses.activate
def test_collect_filters_window_and_tolerates_failed_feed() -> None:
    good = Feed("Good", "https://good.test/rss", 1.0)
    bad = Feed("Bad", "https://bad.test/rss", 1.0)
    responses.add(responses.GET, good.url, body=RSS)
    responses.add(responses.GET, bad.url, status=404)
    stories, ok = sources.collect((good, bad), now=NOW, lookback_hours=36)
    assert ok == 1
    assert [s.title for s in stories] == ["Big & Bold Launch"]  # old + undated excluded


@responses.activate
def test_collect_all_failed_raises() -> None:
    responses.add(responses.GET, FEED.url, body=requests.ConnectionError("boom"))
    with pytest.raises(CollectionError, match="All news feeds failed"):
        sources.collect((FEED,), now=NOW)


@responses.activate
def test_fetch_retries_transient_then_succeeds() -> None:
    responses.add(responses.GET, FEED.url, status=503)
    responses.add(responses.GET, FEED.url, body=b"ok")
    assert sources.fetch_feed(requests.Session(), FEED) == b"ok"
    assert len(responses.calls) == 2


@responses.activate
def test_fetch_does_not_retry_404() -> None:
    responses.add(responses.GET, FEED.url, status=404)
    with pytest.raises(CollectionError):
        sources.fetch_feed(requests.Session(), FEED)
    assert len(responses.calls) == 1


def test_future_dated_items_excluded() -> None:
    future = (NOW + timedelta(days=3)).strftime("%a, %d %b %Y %H:%M:%S GMT").encode()
    xml = (
        b'<rss version="2.0"><channel><item><title>Future</title><link>https://x.test/f</link>'
        b"<pubDate>" + future + b"</pubDate></item></channel></rss>"
    )
    feed = Feed("F", "https://f.test/rss", 1.0)
    with responses.RequestsMock() as rsps:
        rsps.add(responses.GET, feed.url, body=xml)
        stories, _ = sources.collect((feed,), now=NOW)
    assert stories == []
