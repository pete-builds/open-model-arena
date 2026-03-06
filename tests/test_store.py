"""Tests for the Store (database layer)."""

import pytest


@pytest.mark.asyncio
async def test_create_and_get_battle(test_store):
    battle_id = await test_store.create_battle("hello world", "general", "model-alpha", "model-beta")
    assert len(battle_id) == 16

    battle = await test_store.get_battle(battle_id)
    assert battle is not None
    assert battle["prompt"] == "hello world"
    assert battle["category"] == "general"
    assert battle["model_a"] == "model-alpha"
    assert battle["model_b"] == "model-beta"
    assert battle["winner"] is None


@pytest.mark.asyncio
async def test_get_nonexistent_battle(test_store):
    battle = await test_store.get_battle("doesnotexist")
    assert battle is None


@pytest.mark.asyncio
async def test_update_responses(test_store):
    battle_id = await test_store.create_battle("test", "general", "model-alpha", "model-beta")

    await test_store.update_response_a(battle_id, "response from A", 500, 100, 0.003)
    await test_store.update_response_b(battle_id, "response from B", 700, 150, 0.005)

    battle = await test_store.get_battle(battle_id)
    assert battle["response_a"] == "response from A"
    assert battle["response_b"] == "response from B"
    assert battle["latency_a_ms"] == 500
    assert battle["latency_b_ms"] == 700
    assert battle["tokens_a"] == 100
    assert battle["tokens_b"] == 150
    assert battle["cost_a"] == 0.003
    assert battle["cost_b"] == 0.005


@pytest.mark.asyncio
async def test_record_vote(test_store):
    battle_id = await test_store.create_battle("test", "general", "model-alpha", "model-beta")
    await test_store.update_response_a(battle_id, "A says hi", 300, 50, 0.001)
    await test_store.update_response_b(battle_id, "B says hi", 400, 60, 0.002)

    results = await test_store.record_vote(battle_id, "a")

    assert results["rating_a_before"] == 1500.0
    assert results["rating_b_before"] == 1500.0
    assert results["rating_a_after"] > 1500.0
    assert results["rating_b_after"] < 1500.0

    battle = await test_store.get_battle(battle_id)
    assert battle["winner"] == "a"
    assert battle["voted_at"] is not None


@pytest.mark.asyncio
async def test_double_vote_rejected(test_store):
    battle_id = await test_store.create_battle("test", "general", "model-alpha", "model-beta")
    await test_store.record_vote(battle_id, "a")

    with pytest.raises(ValueError, match="already voted"):
        await test_store.record_vote(battle_id, "b")


@pytest.mark.asyncio
async def test_vote_on_missing_battle(test_store):
    with pytest.raises(ValueError, match="battle not found"):
        await test_store.record_vote("nonexistent", "a")


@pytest.mark.asyncio
async def test_leaderboard_after_votes(test_store):
    # Run a few battles
    for _ in range(3):
        bid = await test_store.create_battle("q", "general", "model-alpha", "model-beta")
        await test_store.update_response_a(bid, "a", 300, 50, 0.001)
        await test_store.update_response_b(bid, "b", 400, 60, 0.002)
        await test_store.record_vote(bid, "a")

    leaderboard = await test_store.get_leaderboard("overall")
    assert len(leaderboard) == 2

    # Alpha should be ranked higher (won all 3)
    assert leaderboard[0]["model_id"] == "model-alpha"
    assert leaderboard[0]["wins"] == 3
    assert leaderboard[0]["rating"] > 1500.0

    assert leaderboard[1]["model_id"] == "model-beta"
    assert leaderboard[1]["losses"] == 3
    assert leaderboard[1]["rating"] < 1500.0


@pytest.mark.asyncio
async def test_tie_vote(test_store):
    bid = await test_store.create_battle("q", "general", "model-alpha", "model-beta")
    results = await test_store.record_vote(bid, "tie")

    # Tie between equal ratings = no change
    assert results["rating_a_after"] == 1500.0
    assert results["rating_b_after"] == 1500.0


@pytest.mark.asyncio
async def test_stats(test_store):
    stats = await test_store.get_stats()
    assert stats["total_battles"] == 0
    assert stats["total_voted"] == 0

    bid = await test_store.create_battle("q", "general", "model-alpha", "model-beta")
    stats = await test_store.get_stats()
    assert stats["total_battles"] == 1
    assert stats["total_voted"] == 0

    await test_store.record_vote(bid, "a")
    stats = await test_store.get_stats()
    assert stats["total_voted"] == 1


@pytest.mark.asyncio
async def test_export_voted_battles(test_store):
    bid = await test_store.create_battle("prompt1", "coding", "model-alpha", "model-beta")
    await test_store.record_vote(bid, "b")

    # Unvoted battle should not appear
    await test_store.create_battle("prompt2", "general", "model-alpha", "model-beta")

    exports = await test_store.get_all_voted_battles()
    assert len(exports) == 1
    assert exports[0]["prompt"] == "prompt1"
    assert exports[0]["winner"] == "b"


@pytest.mark.asyncio
async def test_category_ratings_tracked_separately(test_store):
    bid = await test_store.create_battle("q", "coding", "model-alpha", "model-beta")
    await test_store.record_vote(bid, "a")

    overall = await test_store.get_leaderboard("overall")
    coding = await test_store.get_leaderboard("coding")

    # Both should have entries
    assert len(overall) == 2
    assert len(coding) == 2

    # General should be empty (different category)
    general = await test_store.get_leaderboard("general")
    assert len(general) == 0
