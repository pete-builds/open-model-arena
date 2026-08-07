"""Tests for the Store (database layer)."""

import asyncio

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
async def test_concurrent_double_vote_race(test_store):
    """Two votes racing on the same battle: exactly one wins, ratings move once.

    The sequential double-vote test can't catch the TOCTOU race in record_vote
    because it never interleaves at the await boundaries. This fires both votes
    concurrently via asyncio.gather and asserts the atomic claim holds.
    """
    battle_id = await test_store.create_battle("race", "general", "model-alpha", "model-beta")
    await test_store.update_response_a(battle_id, "A", 300, 50, 0.001)
    await test_store.update_response_b(battle_id, "B", 400, 60, 0.002)

    results = await asyncio.gather(
        test_store.record_vote(battle_id, "a"),
        test_store.record_vote(battle_id, "b"),
        return_exceptions=True,
    )

    successes = [r for r in results if isinstance(r, dict)]
    failures = [r for r in results if isinstance(r, ValueError)]
    assert len(successes) == 1, f"expected exactly one success, got {results}"
    assert len(failures) == 1, f"expected exactly one failure, got {results}"
    assert str(failures[0]) == "already voted"

    # vote_log has exactly one row for this battle.
    cursor = await test_store.db.execute("SELECT COUNT(*) AS c FROM vote_log WHERE battle_id = ?", (battle_id,))
    assert (await cursor.fetchone())["c"] == 1

    # Ratings moved exactly once (one win, one loss; no double-count).
    overall = await test_store.get_leaderboard("overall")
    assert {r["wins"] for r in overall} == {0, 1}
    assert {r["losses"] for r in overall} == {0, 1}
    assert sorted(round(r["rating"], 4) for r in overall) == sorted([round(1500.0 - 16.0, 4), round(1500.0 + 16.0, 4)])


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


@pytest.mark.asyncio
async def test_vote_with_overall_category_not_double_counted(test_store):
    """A battle created with category='overall' must apply exactly one Elo delta."""
    bid = await test_store.create_battle("q", "overall", "model-alpha", "model-beta")
    results = await test_store.record_vote(bid, "a")

    # Rating moved once, not twice: a single win at k=32 with equal starting
    # ratings gives a 16-point swing; a double-apply would land near 32 points.
    assert abs((results["rating_a_after"] - 1500.0) - 16.0) < 1e-6
    assert abs((1500.0 - results["rating_b_after"]) - 16.0) < 1e-6

    overall = await test_store.get_leaderboard("overall")
    winner = next(r for r in overall if r["model_id"] == "model-alpha")
    loser = next(r for r in overall if r["model_id"] == "model-beta")
    assert winner["wins"] == 1
    assert loser["losses"] == 1


@pytest.mark.asyncio
async def test_claim_battle_execution_is_atomic(test_store):
    """Only one of many concurrent claims wins; the rest report state='running'."""
    bid = await test_store.create_battle("q", "general", "model-alpha", "model-beta")

    results = await asyncio.gather(*[test_store.claim_battle_execution(bid) for _ in range(5)])
    claimed = [r for r in results if r[0]]
    refused = [r for r in results if not r[0]]

    assert len(claimed) == 1
    assert len(refused) == 4
    assert all(state == "running" for _, state in refused)


@pytest.mark.asyncio
async def test_claim_battle_execution_refuses_after_complete(test_store):
    """Once marked complete, a battle cannot be re-claimed for execution."""
    bid = await test_store.create_battle("q", "general", "model-alpha", "model-beta")
    claimed, _ = await test_store.claim_battle_execution(bid)
    assert claimed
    await test_store.mark_battle_execution(bid, "complete")

    claimed2, state = await test_store.claim_battle_execution(bid)
    assert not claimed2
    assert state == "complete"


@pytest.mark.asyncio
async def test_concurrent_votes_across_battles_preserve_ratings(test_store):
    """Concurrent votes for different battles must not lose Elo updates.

    With a shared aiosqlite connection and no locking, RMW rating updates
    interleave: two votes both read 1500, both compute 1516/1484, and the
    second write clobbers the first. Serializing writes fixes this — the
    second vote reads the post-first-vote rating and stacks the delta.
    """
    # Two battles, same models, both voted for "a".
    bids = [await test_store.create_battle(f"q{i}", "general", "model-alpha", "model-beta") for i in range(2)]

    await asyncio.gather(*[test_store.record_vote(bid, "a") for bid in bids])

    board = {r["model_id"]: r for r in await test_store.get_leaderboard("overall")}
    # Two wins for alpha, two losses for beta, both counters exactly right.
    assert board["model-alpha"]["wins"] == 2
    assert board["model-alpha"]["losses"] == 0
    assert board["model-beta"]["wins"] == 0
    assert board["model-beta"]["losses"] == 2
    # Two stacked +16 deltas would land near 1531.5 (second delta is slightly
    # smaller because the expected score shifts). A single interleaved delta
    # would leave alpha at 1516 — well below the >1520 floor asserted here.
    assert board["model-alpha"]["rating"] > 1520.0
    assert board["model-beta"]["rating"] < 1480.0


@pytest.mark.asyncio
async def test_concurrent_update_and_vote_are_isolated(test_store):
    """A late update_response_a racing with record_vote must not corrupt vote_log."""
    bid = await test_store.create_battle("q", "general", "model-alpha", "model-beta")
    await test_store.update_response_a(bid, "A initial", 100, 10, 0.001)
    await test_store.update_response_b(bid, "B initial", 100, 10, 0.001)

    # Fire a vote and a stream-late response update at the same time. Without
    # a write lock the update's commit() could flush the vote's partial state.
    await asyncio.gather(
        test_store.record_vote(bid, "a"),
        test_store.update_response_a(bid, "A late", 200, 20, 0.002),
    )

    # Exactly one vote_log row exists for this battle regardless of ordering.
    cursor = await test_store.db.execute("SELECT COUNT(*) AS c FROM vote_log WHERE battle_id = ?", (bid,))
    assert (await cursor.fetchone())["c"] == 1

    battle = await test_store.get_battle(bid)
    assert battle["winner"] == "a"
    # Elo delta is consistent with a single "a" win at 1500 vs 1500.
    log = await test_store.get_vote_log(bid)
    assert abs((log["rating_a_after"] - 1500.0) - 16.0) < 1e-6
