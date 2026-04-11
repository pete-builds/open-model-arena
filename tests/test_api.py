"""API endpoint tests using FastAPI TestClient."""

import os

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# These must be set before importing main
os.environ.setdefault("ARENA_PASSPHRASE", "test-passphrase")
os.environ.setdefault("AUTH_TOKEN_SECRET", "test-secret-key")

from app.main import ARENA_PASSPHRASE, _make_token, app, store


def _auth_cookies() -> dict[str, str]:
    """Generate valid auth + CSRF cookies for testing."""
    token = _make_token(ARENA_PASSPHRASE)
    csrf = "test-csrf-token-abc123"
    return {"arena_token": token, "arena_csrf": csrf}


def _cookie_header(cookies: dict[str, str]) -> str:
    return "; ".join(f"{k}={v}" for k, v in cookies.items())


@pytest.fixture
def auth_headers():
    cookies = _auth_cookies()
    return {
        "cookie": _cookie_header(cookies),
        "x-csrf-token": cookies["arena_csrf"],
    }


@pytest.fixture
def auth_headers_get():
    """GET requests don't need CSRF."""
    cookies = _auth_cookies()
    return {"cookie": _cookie_header(cookies)}


@pytest_asyncio.fixture
async def client():
    """Async test client with DB initialized."""
    # Use temp DB for tests
    import tempfile
    original_path = store.db_path
    store.db_path = tempfile.mktemp(suffix=".db")

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        # Manually run startup (lifespan)
        await store.connect()
        yield ac
        await store.close()

    store.db_path = original_path


# --- Health Check ---

@pytest.mark.asyncio
async def test_healthz(client):
    resp = await client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# --- Auth ---

@pytest.mark.asyncio
async def test_unauthenticated_api_returns_401(client):
    resp = await client.get("/api/models")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_unauthenticated_page_redirects_to_login(client):
    resp = await client.get("/leaderboard", follow_redirects=False)
    assert resp.status_code == 307
    assert "/login" in resp.headers["location"]


@pytest.mark.asyncio
async def test_login_success(client):
    resp = await client.post("/api/login", json={"passphrase": "test-passphrase"})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert "arena_token" in resp.cookies
    assert "arena_csrf" in resp.cookies


@pytest.mark.asyncio
async def test_login_wrong_passphrase(client):
    resp = await client.post("/api/login", json={"passphrase": "wrong"})
    assert resp.status_code == 401


# --- Models ---

@pytest.mark.asyncio
async def test_list_models(client, auth_headers_get):
    resp = await client.get("/api/models", headers=auth_headers_get)
    assert resp.status_code == 200
    models = resp.json()
    assert isinstance(models, list)
    assert len(models) > 0
    assert all("id" in m and "display_name" in m for m in models)


# --- Battle Flow ---

@pytest.mark.asyncio
async def test_create_battle(client, auth_headers):
    resp = await client.post(
        "/api/battle",
        json={"prompt": "What is Python?", "category": "general"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "battle_id" in data
    assert len(data["battle_id"]) == 16


@pytest.mark.asyncio
async def test_create_battle_empty_prompt(client, auth_headers):
    resp = await client.post(
        "/api/battle",
        json={"prompt": "   ", "category": "general"},
        headers=auth_headers,
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_create_battle_too_long(client, auth_headers):
    resp = await client.post(
        "/api/battle",
        json={"prompt": "x" * 10001, "category": "general"},
        headers=auth_headers,
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_create_battle_same_model(client, auth_headers):
    resp = await client.post(
        "/api/battle",
        json={"prompt": "test", "model_a": "model-alpha", "model_b": "model-alpha"},
        headers=auth_headers,
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_vote_missing_battle(client, auth_headers):
    resp = await client.post(
        "/api/battle/abcdefghij123456/vote",
        json={"winner": "a"},
        headers=auth_headers,
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_vote_invalid_battle_id_format(client, auth_headers):
    resp = await client.post(
        "/api/battle/not-valid!/vote",
        json={"winner": "a"},
        headers=auth_headers,
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_vote_invalid_winner(client, auth_headers):
    # Create a battle first
    create_resp = await client.post(
        "/api/battle",
        json={"prompt": "test", "category": "general"},
        headers=auth_headers,
    )
    battle_id = create_resp.json()["battle_id"]

    resp = await client.post(
        f"/api/battle/{battle_id}/vote",
        json={"winner": "c"},
        headers=auth_headers,
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_csrf_required_on_post(client):
    """POST with valid auth cookie but missing CSRF should be rejected."""
    cookies = _auth_cookies()
    headers = {"cookie": _cookie_header(cookies)}  # no x-csrf-token
    resp = await client.post(
        "/api/battle",
        json={"prompt": "test", "category": "general"},
        headers=headers,
    )
    assert resp.status_code == 403


# --- Leaderboard ---

@pytest.mark.asyncio
async def test_leaderboard_empty(client, auth_headers_get):
    resp = await client.get("/api/leaderboard", headers=auth_headers_get)
    assert resp.status_code == 200
    assert resp.json() == []


# --- Stats ---

@pytest.mark.asyncio
async def test_stats(client, auth_headers_get):
    resp = await client.get("/api/stats", headers=auth_headers_get)
    assert resp.status_code == 200
    data = resp.json()
    assert "total_battles" in data
    assert "total_voted" in data
    assert "battles_today" in data


# --- Export ---

@pytest.mark.asyncio
async def test_export_json(client, auth_headers_get):
    resp = await client.get("/api/export?format=json", headers=auth_headers_get)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_export_csv(client, auth_headers_get):
    resp = await client.get("/api/export?format=csv", headers=auth_headers_get)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_export_invalid_format(client, auth_headers_get):
    resp = await client.get("/api/export?format=xml", headers=auth_headers_get)
    assert resp.status_code == 400


# --- Client IP extraction ---


@pytest.mark.asyncio
async def test_create_battle_uses_forwarded_ip(client, auth_headers):
    """X-Forwarded-For header is used for rate limiting."""
    headers = {**auth_headers, "x-forwarded-for": "203.0.113.1, 10.0.0.1"}
    resp = await client.post(
        "/api/battle",
        json={"prompt": "test ip", "category": "general"},
        headers=headers,
    )
    assert resp.status_code == 200


# --- Battle with specific models ---


@pytest.mark.asyncio
async def test_create_battle_nonexistent_model(client, auth_headers):
    resp = await client.post(
        "/api/battle",
        json={"prompt": "test", "model_a": "no-such-model", "model_b": "model-alpha"},
        headers=auth_headers,
    )
    assert resp.status_code == 400
    assert "model not found" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_create_battle_specific_models(client, auth_headers):
    resp = await client.post(
        "/api/battle",
        json={"prompt": "test", "model_a": "gpt-4o", "model_b": "gpt-4o-mini"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert "battle_id" in resp.json()


# --- Stream endpoint ---


@pytest.mark.asyncio
async def test_stream_invalid_battle_id(client, auth_headers_get):
    resp = await client.get("/api/battle/invalid!/stream", headers=auth_headers_get)
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_stream_nonexistent_battle(client, auth_headers_get):
    resp = await client.get("/api/battle/abcdefghij123456/stream", headers=auth_headers_get)
    assert resp.status_code == 404


# --- Full vote flow ---


@pytest.mark.asyncio
async def test_full_vote_flow(client, auth_headers):
    """Create a battle, store responses, vote, and check reveal data."""
    # Create
    resp = await client.post(
        "/api/battle",
        json={"prompt": "Compare Python and Go", "category": "general"},
        headers=auth_headers,
    )
    battle_id = resp.json()["battle_id"]

    # Manually store responses so vote works
    await store.update_response_a(battle_id, "Python is great", 300, 50, 0.001)
    await store.update_response_b(battle_id, "Go is fast", 200, 40, 0.0005)

    # Vote
    resp = await client.post(
        f"/api/battle/{battle_id}/vote",
        json={"winner": "a"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "model_a_name" in data
    assert "model_b_name" in data
    assert data["rating_a_after"] > data["rating_a_before"]


@pytest.mark.asyncio
async def test_double_vote_rejected(client, auth_headers):
    resp = await client.post(
        "/api/battle",
        json={"prompt": "test", "category": "general"},
        headers=auth_headers,
    )
    battle_id = resp.json()["battle_id"]

    # First vote
    await client.post(
        f"/api/battle/{battle_id}/vote",
        json={"winner": "a"},
        headers=auth_headers,
    )
    # Second vote
    resp = await client.post(
        f"/api/battle/{battle_id}/vote",
        json={"winner": "b"},
        headers=auth_headers,
    )
    assert resp.status_code == 400
    assert "already voted" in resp.json()["detail"]


# --- Leaderboard with data ---


@pytest.mark.asyncio
async def test_leaderboard_with_ranked_and_provisional(client, auth_headers, auth_headers_get):
    """After enough votes, models appear as ranked; others stay provisional."""
    # Create 6 battles directly in the store to avoid rate limiter
    for _ in range(6):
        bid = await store.create_battle("test", "general", "gpt-4o", "gpt-4o-mini")
        await store.update_response_a(bid, "a", 300, 50, 0.001)
        await store.update_response_b(bid, "b", 400, 60, 0.002)
        await store.record_vote(bid, "a")

    resp = await client.get("/api/leaderboard", headers=auth_headers_get)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 2

    # Check ranked entries have rank numbers
    ranked = [d for d in data if not d["provisional"]]
    assert len(ranked) >= 1
    assert ranked[0]["rank"] is not None
    assert ranked[0]["win_rate"] > 0


# --- Export with data ---


@pytest.mark.asyncio
async def test_export_csv_with_data(client, auth_headers, auth_headers_get):
    """CSV export includes battle data with model names."""
    bid = await store.create_battle("export test", "general", "gpt-4o", "gpt-4o-mini")
    await store.update_response_a(bid, "a", 300, 50, 0.001)
    await store.update_response_b(bid, "b", 400, 60, 0.002)
    await store.record_vote(bid, "b")

    resp = await client.get("/api/export?format=csv", headers=auth_headers_get)
    assert resp.status_code == 200
    assert "text/csv" in resp.headers["content-type"]
    body = resp.text
    assert "export test" in body
    assert "model_a_name" in body


# --- SPA routes ---


@pytest.mark.asyncio
async def test_battle_page_valid_id(client, auth_headers_get):
    resp = await client.get("/battle/abcdefghij123456", headers=auth_headers_get)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_battle_page_invalid_id(client, auth_headers_get):
    resp = await client.get("/battle/bad!", headers=auth_headers_get)
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_leaderboard_page(client, auth_headers_get):
    resp = await client.get("/leaderboard", headers=auth_headers_get)
    assert resp.status_code == 200
