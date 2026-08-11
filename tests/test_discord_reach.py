"""Unit tests for the pure Discord-reach planning helpers (no network, no DB)."""

from research_engine import discord_reach as dr


def test_suggest_channels_game_topic_includes_game_channels():
    chans = dr.suggest_channels("Marvel's Wolverine game")
    assert "#games" in chans or "#video-games" in chans
    assert "#general" in chans


def test_suggest_channels_single_word_offers_topic_channel_first():
    chans = dr.suggest_channels("Wolverine")
    assert chans[0] == "#wolverine"


def test_search_terms_leads_with_topic_and_drops_stopwords():
    terms = dr.search_terms("Marvel's Wolverine game")
    assert terms[0] == "Marvel's Wolverine game"
    # "game" is a stopword and should not appear as its own term
    assert "game" not in [t.lower() for t in terms]
    # a meaningful word survives
    assert any(t.lower() == "wolverine" for t in terms)


def test_search_terms_empty_topic():
    assert dr.search_terms("") == []


def test_top_communities_ranks_by_online_then_members_and_drops_unreachable():
    communities = [
        {"name": "small-live", "members": 100, "online": 90, "invite_code": "a"},
        {"name": "huge-quiet", "members": 900_000, "online": 50, "invite_code": "b"},
        {"name": "no-invite", "members": 999_999, "online": 999_999},  # unreachable -> dropped
    ]
    top = dr.top_communities(communities, n=3)
    names = [c["name"] for c in top]
    assert "no-invite" not in names
    assert names[0] == "small-live"  # more online wins over more members
    assert names[1] == "huge-quiet"


def test_format_capture_plan_has_links_terms_and_armed_notice():
    communities = [{"name": "Marvel", "members": 400_000, "online": 20_000, "invite_code": "marvel"}]
    plan = dr.format_capture_plan(
        "Wolverine game", communities, ["#games", "#general"], ["Wolverine", "Insomniac"], armed=True)
    assert "[Marvel](https://discord.gg/marvel)" in plan
    assert "400,000 members" in plan and "20,000 online" in plan
    assert "`Wolverine`" in plan and "`Insomniac`" in plan
    assert "search bar" in plan.lower()
    assert "armed" in plan.lower()
    assert "☐" in plan  # rendered as a checklist of tabs to open
    # never claims to act for the user
    assert "never open, search, or scroll for you" in plan.lower()


def test_format_capture_plan_empty_is_honest_not_a_failure():
    plan = dr.format_capture_plan("obscure thing", [], ["#general"], ["obscure thing"], armed=False)
    assert "no public discord servers" in plan.lower()
    assert "reddit" in plan.lower()  # points to the better path
