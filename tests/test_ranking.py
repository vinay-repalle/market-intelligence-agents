from datetime import timedelta

from ai_market_agent import ranking
from conftest import NOW, make_story


def test_relevance_filter() -> None:
    off = make_story("Local bakery wins award", ai_only=False, snippet="Bread.")
    on = make_story("Bakery adopts AI ordering", ai_only=False)
    assert not ranking.is_relevant(off)
    assert ranking.is_relevant(on)
    assert ranking.is_relevant(make_story("Anything", ai_only=True))


def test_dedupe_same_url_and_similar_title() -> None:
    a = make_story("OpenAI launches GPT-6 model", source="OpenAI", source_weight=1.0)
    b = make_story("OpenAI launches GPT-6 model today", source="Verge", url="https://v.test/1")
    c = make_story("Totally different funding news", url="https://c.test/1")
    d = make_story("Unrelated title entirely", url=a.url, source="Mirror")
    out = ranking.dedupe([b, c, a, d])
    assert len(out) == 2
    keeper = next(s for s in out if s.url == a.url)
    assert keeper.source == "OpenAI"  # highest trust kept
    assert set(keeper.also_covered_by) == {"Verge", "Mirror"}


def test_recent_beats_old_all_else_equal() -> None:
    fresh = make_story("Company announces tool", published=NOW - timedelta(hours=1))
    stale = make_story("Company announces gadget", published=NOW - timedelta(hours=30))
    assert ranking.score(fresh, NOW) > ranking.score(stale, NOW)


def test_launch_beats_fluff_and_official_boost() -> None:
    big = make_story("Startup raises $100M and launches model")
    meh = make_story("Opinion: musings on the week", snippet="Thoughts.")
    assert ranking.score(big, NOW) > ranking.score(meh, NOW)
    assert ranking.score(make_story(official=True), NOW) > ranking.score(make_story(), NOW)


def test_coverage_boosts() -> None:
    solo = make_story("Alpha introduces thing")
    multi = make_story("Alpha introduces thing", also_covered_by=["A", "B"])
    assert ranking.score(multi, NOW) > ranking.score(solo, NOW)


def test_rank_limits_and_orders() -> None:
    stories = [
        make_story(f"Vendor{i} releases product number{i}", published=NOW - timedelta(hours=i))
        for i in range(15)
    ]
    top = ranking.rank(stories, 10, NOW)
    assert len(top) == 10
    scores = [s.score for s in top]
    assert scores == sorted(scores, reverse=True)


def test_rank_returns_fewer_when_few() -> None:
    assert len(ranking.rank([make_story()], 10, NOW)) == 1
    assert ranking.rank([], 10, NOW) == []


def test_per_source_cap_and_backfill() -> None:
    many = [make_story(f"Alpha{i} launches thing{i}", source="Loud") for i in range(6)]
    other = [make_story("Beta releases model", source="Quiet", url="https://q.test/1")]
    top = ranking.rank(many + other, 4, NOW)
    assert sum(s.source == "Loud" for s in top) == 3 and len(top) == 4
    # with too few alternatives, backfill rather than send fewer than available
    assert len(ranking.rank(many, 5, NOW)) == 5
