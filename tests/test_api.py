"""API endpoint tests using FastAPI TestClient."""

import asyncio
import os

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# These must be set before importing main
os.environ.setdefault("ARENA_PASSPHRASE", "test-passphrase")
os.environ.setdefault("AUTH_TOKEN_SECRET", "test-secret-key")

from app import clientip, runtime
from app.main import ARENA_PASSPHRASE, _make_token, app, battle_limiter, store


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
    # Reset the in-memory rate limiter so per-test POST counts don't collide
    # across the suite (all tests share the same "unknown" client IP).
    battle_limiter.requests.clear()

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


def _mock_request(peer: str | None, xff: str | None):
    from starlette.requests import Request

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(b"x-forwarded-for", xff.encode())] if xff else [],
        "client": (peer, 12345) if peer else None,
    }
    return Request(scope)


def test_get_client_ip_ignores_xff_from_untrusted_peer(monkeypatch):
    """Default (no TRUSTED_PROXIES): XFF is ignored, socket peer wins."""
    from app import main

    monkeypatch.setattr(clientip, "TRUSTED_PROXIES", [])
    req = _mock_request(peer="203.0.113.99", xff="1.2.3.4, 5.6.7.8")
    assert main._get_client_ip(req) == "203.0.113.99"


def test_get_client_ip_honors_xff_from_trusted_peer(monkeypatch):
    """When the peer is in TRUSTED_PROXIES, the first XFF hop is trusted."""
    import ipaddress

    from app import main

    monkeypatch.setattr(clientip, "TRUSTED_PROXIES", [ipaddress.ip_network("10.0.0.0/8")])
    req = _mock_request(peer="10.0.0.5", xff="1.2.3.4, 5.6.7.8")
    assert main._get_client_ip(req) == "1.2.3.4"


def test_get_client_ip_falls_back_when_no_xff_from_trusted_peer(monkeypatch):
    """Trusted peer with no XFF header: peer is the client."""
    import ipaddress

    from app import main

    monkeypatch.setattr(clientip, "TRUSTED_PROXIES", [ipaddress.ip_network("10.0.0.0/8")])
    req = _mock_request(peer="10.0.0.5", xff=None)
    assert main._get_client_ip(req) == "10.0.0.5"


def test_xff_rate_limit_bypass_defense(monkeypatch):
    """A client rotating XFF from an untrusted peer must not shake off the limiter."""
    from app import main

    monkeypatch.setattr(clientip, "TRUSTED_PROXIES", [])
    main.battle_limiter.requests.clear()

    for i in range(15):
        req = _mock_request(peer="203.0.113.42", xff=f"1.2.3.{i}")
        # Every call, even with a fresh spoofed XFF, keys the same socket peer.
        main.battle_limiter.is_allowed(main._get_client_ip(req))

    # Same peer keyed 15 times against a 10-request limit → next call blocked.
    req = _mock_request(peer="203.0.113.42", xff="9.9.9.9")
    assert main.battle_limiter.is_allowed(main._get_client_ip(req)) is False


# --- Category validation ---


@pytest.mark.asyncio
async def test_create_battle_rejects_unknown_category(client, auth_headers):
    resp = await client.post(
        "/api/battle",
        json={"prompt": "test", "category": "definitely-not-a-real-category"},
        headers=auth_headers,
    )
    assert resp.status_code == 400
    assert "unknown category" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_create_battle_rejects_overall_as_category(client, auth_headers):
    """'overall' is the aggregate bucket, never a per-battle category."""
    resp = await client.post(
        "/api/battle",
        json={"prompt": "test", "category": "overall"},
        headers=auth_headers,
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_create_battle_explicit_models_reject_category_they_dont_support(client, auth_headers, monkeypatch):
    """When both models are named, the category must be advertised by both."""
    from app import main
    from app.config import Model

    narrow = Model(
        id="narrow-model",
        provider_name=next(iter(main.config.providers)),
        display_name="Narrow",
        model_id="narrow",
        input_cost_per_1m=0.0,
        output_cost_per_1m=0.0,
        categories=["general"],
        enabled=True,
    )
    monkeypatch.setattr(main.config, "models", main.config.models + [narrow], raising=False)

    resp = await client.post(
        "/api/battle",
        json={"prompt": "test", "category": "coding", "model_a": "narrow-model", "model_b": "gpt-4o"},
        headers=auth_headers,
    )
    assert resp.status_code == 400
    assert "does not support category" in resp.json()["detail"]


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


# --- Permalink (GET /api/battle/{id}) ---


@pytest.mark.asyncio
async def test_get_battle_invalid_id_format(client, auth_headers_get):
    resp = await client.get("/api/battle/bad!", headers=auth_headers_get)
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_get_battle_nonexistent(client, auth_headers_get):
    resp = await client.get("/api/battle/abcdefghij123456", headers=auth_headers_get)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_battle_unvoted_returns_404(client, auth_headers, auth_headers_get):
    """A battle without a vote isn't a valid permalink target — never leak in-flight state."""
    create_resp = await client.post(
        "/api/battle",
        json={"prompt": "unvoted", "category": "general"},
        headers=auth_headers,
    )
    battle_id = create_resp.json()["battle_id"]

    resp = await client.get(f"/api/battle/{battle_id}", headers=auth_headers_get)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_battle_voted_returns_full_reveal(client, auth_headers, auth_headers_get):
    """A voted battle round-trips to a fully-populated reveal payload."""
    create_resp = await client.post(
        "/api/battle",
        json={"prompt": "explain closures", "category": "coding"},
        headers=auth_headers,
    )
    battle_id = create_resp.json()["battle_id"]

    await store.update_response_a(battle_id, "response from A", 500, 100, 0.003)
    await store.update_response_b(battle_id, "response from B", 700, 150, 0.005)

    vote_resp = await client.post(
        f"/api/battle/{battle_id}/vote",
        json={"winner": "a"},
        headers=auth_headers,
    )
    assert vote_resp.status_code == 200

    resp = await client.get(f"/api/battle/{battle_id}", headers=auth_headers_get)
    assert resp.status_code == 200
    data = resp.json()

    assert data["id"] == battle_id
    assert data["prompt"] == "explain closures"
    assert data["category"] == "coding"
    assert data["response_a"] == "response from A"
    assert data["response_b"] == "response from B"
    assert data["winner"] == "a"
    assert data["latency_a_ms"] == 500
    assert data["latency_b_ms"] == 700
    assert data["tokens_a"] == 100
    assert data["tokens_b"] == 150
    assert data["cost_a"] == 0.003
    assert data["cost_b"] == 0.005
    assert data["model_a_name"] and data["model_b_name"]
    assert data["model_a_provider"] and data["model_b_provider"]
    # ELO deltas — must be populated because record_vote just wrote vote_log
    assert data["rating_a_before"] == 1500.0
    assert data["rating_b_before"] == 1500.0
    assert data["rating_a_after"] > 1500.0
    assert data["rating_b_after"] < 1500.0


@pytest.mark.asyncio
async def test_get_battle_requires_auth(client):
    resp = await client.get("/api/battle/abcdefghij123456")
    assert resp.status_code == 401


# --- Bearer-token API auth ---


@pytest.mark.asyncio
async def test_bearer_token_allows_get_without_cookie(client, monkeypatch):
    from app import main

    monkeypatch.setattr(main, "API_TOKENS", ["test-bearer-1"])
    resp = await client.get("/api/models", headers={"authorization": "Bearer test-bearer-1"})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_bearer_token_x_api_token_header_also_works(client, monkeypatch):
    from app import main

    monkeypatch.setattr(main, "API_TOKENS", ["test-bearer-2"])
    resp = await client.get("/api/models", headers={"x-api-token": "test-bearer-2"})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_bearer_token_wrong_value_rejected(client, monkeypatch):
    from app import main

    monkeypatch.setattr(main, "API_TOKENS", ["only-me"])
    resp = await client.get("/api/models", headers={"authorization": "Bearer nope"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_bearer_token_post_skips_csrf(client, monkeypatch):
    """Bearer clients can't carry a CSRF double-submit; auth must let them through."""
    from app import main

    monkeypatch.setattr(main, "API_TOKENS", ["ci-bot"])
    resp = await client.post(
        "/api/battle",
        json={"prompt": "hi", "category": "general"},
        headers={"authorization": "Bearer ci-bot"},
    )
    assert resp.status_code == 200
    assert "battle_id" in resp.json()


@pytest.mark.asyncio
async def test_no_bearer_configured_blocks_bearer_path(client, monkeypatch):
    """When ARENA_API_TOKENS is empty, bearer requests fall through to cookie auth (which rejects)."""
    from app import main

    monkeypatch.setattr(main, "API_TOKENS", [])
    resp = await client.get("/api/models", headers={"authorization": "Bearer anything"})
    assert resp.status_code == 401


# --- Features endpoint ---


@pytest.mark.asyncio
async def test_features_reports_judge_off_by_default(client, auth_headers_get):
    resp = await client.get("/api/features", headers=auth_headers_get)
    assert resp.status_code == 200
    data = resp.json()
    assert "judge" in data
    # example config has no judge configured
    assert data["judge"]["enabled"] is False


# --- Judge endpoint ---


@pytest.mark.asyncio
async def test_judge_endpoint_400_when_not_configured(client, auth_headers, auth_headers_get):
    # Create + populate a battle so the check that fails is judge-config, not battle-state.
    create_resp = await client.post(
        "/api/battle",
        json={"prompt": "explain", "category": "general"},
        headers=auth_headers,
    )
    battle_id = create_resp.json()["battle_id"]
    await store.update_response_a(battle_id, "A ans", 100, 20, 0.0)
    await store.update_response_b(battle_id, "B ans", 200, 30, 0.0)

    resp = await client.post(f"/api/battle/{battle_id}/judge", headers=auth_headers)
    assert resp.status_code == 400
    assert "judge not configured" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_judge_endpoint_end_to_end(client, auth_headers, monkeypatch):
    """With a judge configured and mocked out, /judge casts a vote with method='judge'."""
    from app import main
    from app.config import Judge, Model

    # Inject a fake judge model into the running config
    judge_model = Model(
        id="fake-judge",
        provider_name=next(iter(main.config.providers)),
        display_name="Fake Judge",
        model_id="fake-judge",
        input_cost_per_1m=0.0,
        output_cost_per_1m=0.0,
    )
    monkeypatch.setattr(main.config, "models", main.config.models + [judge_model], raising=False)
    monkeypatch.setattr(main.config, "judge", Judge(model_id="fake-judge", rubric="rubric"), raising=False)

    # Mock run_judge to skip the actual OpenAI call
    async def fake_run_judge(*args, **kwargs):
        return {
            "winner": "b",
            "reasoning": "B answered correctly.",
            "latency_ms": 42,
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "cost": 0.0004,
            "judge_model_id": "fake-judge",
            "judge_display_name": "Fake Judge",
        }

    monkeypatch.setattr(main, "run_judge", fake_run_judge)

    # Set up a battle with responses
    create_resp = await client.post(
        "/api/battle",
        json={"prompt": "which is better?", "category": "general"},
        headers=auth_headers,
    )
    battle_id = create_resp.json()["battle_id"]
    await store.update_response_a(battle_id, "A wins", 100, 20, 0.0)
    await store.update_response_b(battle_id, "B wins", 200, 30, 0.0)

    resp = await client.post(f"/api/battle/{battle_id}/judge", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["vote_method"] == "judge"
    assert data["judge_reasoning"] == "B answered correctly."
    assert data["judge_display_name"] == "Fake Judge"
    assert data["rating_b_after"] > 1500.0  # B won
    assert data["rating_a_after"] < 1500.0

    # Permalink now reports the method + reasoning
    perma = await client.get(f"/api/battle/{battle_id}", headers={"cookie": _cookie_header(_auth_cookies())})
    perma_data = perma.json()
    assert perma_data["vote_method"] == "judge"
    assert perma_data["judge_reasoning"] == "B answered correctly."
    assert perma_data["judge_model_id"] == "fake-judge"


@pytest.mark.asyncio
async def test_judge_endpoint_409_when_already_voted(client, auth_headers, monkeypatch):
    from app import main
    from app.config import Judge, Model

    judge_model = Model(
        id="fake-judge-2",
        provider_name=next(iter(main.config.providers)),
        display_name="Fake",
        model_id="fake",
        input_cost_per_1m=0.0,
        output_cost_per_1m=0.0,
    )
    monkeypatch.setattr(main.config, "models", main.config.models + [judge_model], raising=False)
    monkeypatch.setattr(main.config, "judge", Judge(model_id="fake-judge-2"), raising=False)

    create_resp = await client.post(
        "/api/battle",
        json={"prompt": "q", "category": "general"},
        headers=auth_headers,
    )
    battle_id = create_resp.json()["battle_id"]
    await store.update_response_a(battle_id, "a", 100, 20, 0.0)
    await store.update_response_b(battle_id, "b", 200, 30, 0.0)
    # Cast a human vote first
    vote_resp = await client.post(f"/api/battle/{battle_id}/vote", json={"winner": "a"}, headers=auth_headers)
    assert vote_resp.status_code == 200

    resp = await client.post(f"/api/battle/{battle_id}/judge", headers=auth_headers)
    assert resp.status_code == 409


# --- Suites ---


@pytest.mark.asyncio
async def test_list_suites_empty_by_default(client, auth_headers_get):

    # example config doesn't ship a suites dir loaded in tests
    resp = await client.get("/api/suites", headers=auth_headers_get)
    assert resp.status_code == 200
    # Loaded once at import time — assertion is about API shape, not contents.
    assert isinstance(resp.json(), list)
    assert isinstance(runtime.suites, dict)


@pytest.mark.asyncio
async def test_get_suite_404_for_unknown(client, auth_headers_get, monkeypatch):

    monkeypatch.setattr(runtime, "suites", {})
    resp = await client.get("/api/suites/nope", headers=auth_headers_get)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_run_suite_400_without_judge(client, auth_headers, monkeypatch):
    from app import main
    from app.suites import Suite, SuitePrompt

    monkeypatch.setattr(
        runtime,
        "suites",
        {"tiny": Suite(name="tiny", description="", category="general", prompts=[SuitePrompt(id="p", prompt="q")])},
    )
    monkeypatch.setattr(main.config, "judge", None, raising=False)
    resp = await client.post("/api/suites/tiny/run", headers=auth_headers)
    assert resp.status_code == 400
    assert "judge" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_suite_end_to_end(client, auth_headers, auth_headers_get, monkeypatch):
    """Full suite: kick off, poll until done, verify tally."""
    from app import main
    from app.config import Judge, Model
    from app.suites import Suite, SuitePrompt

    # Two-prompt suite
    suite = Suite(
        name="mini",
        description="",
        category="general",
        prompts=[SuitePrompt(id="p1", prompt="Q1?"), SuitePrompt(id="p2", prompt="Q2?")],
    )
    monkeypatch.setattr(runtime, "suites", {"mini": suite})

    # Configure a fake judge
    judge_model = Model(
        id="fake-suite-judge",
        provider_name=next(iter(main.config.providers)),
        display_name="Suite Judge",
        model_id="fake",
        input_cost_per_1m=0.0,
        output_cost_per_1m=0.0,
    )
    monkeypatch.setattr(main.config, "models", main.config.models + [judge_model], raising=False)
    monkeypatch.setattr(main.config, "judge", Judge(model_id="fake-suite-judge"), raising=False)

    # Fake the actual model calls
    async def fake_run_battle_headless(cfg, st, battle_id):
        return {
            "a": {"response": "A ans", "latency_ms": 100, "tokens": 20, "cost": 0.001},
            "b": {"response": "B ans", "latency_ms": 120, "tokens": 22, "cost": 0.002},
        }

    async def fake_run_judge(*args, **kwargs):
        return {
            "winner": "a",
            "reasoning": "A wins",
            "latency_ms": 40,
            "prompt_tokens": 50,
            "completion_tokens": 10,
            "cost": 0.0005,
            "judge_model_id": "fake-suite-judge",
            "judge_display_name": "Suite Judge",
        }

    from app import routes_suites

    monkeypatch.setattr(routes_suites, "run_battle_headless", fake_run_battle_headless)
    monkeypatch.setattr(routes_suites, "run_judge", fake_run_judge)

    # Kick off the run
    resp = await client.post("/api/suites/mini/run", headers=auth_headers)
    assert resp.status_code == 200
    run_id = resp.json()["run_id"]
    assert resp.json()["battles_total"] == 2

    # Poll — background task should finish fast in tests
    for _ in range(50):
        detail = await client.get(f"/api/suites/runs/{run_id}", headers=auth_headers_get)
        if detail.json().get("status") == "completed":
            break
        await asyncio.sleep(0.02)
    else:
        pytest.fail(f"suite run did not complete in time; last state: {detail.json()}")

    data = detail.json()
    assert data["status"] == "completed"
    assert data["battles_completed"] == 2
    assert data["battles_errored"] == 0
    # 2 battles × (0.001 + 0.002) + 2 judges × 0.0005 = 0.007
    assert data["total_cost"] == pytest.approx(0.007, abs=1e-6)
    assert len(data["battles"]) == 2
    assert all(b["winner"] == "a" for b in data["battles"])
    # Tally should show at least two entries with "a"-side model winning both.
    assert data["tally"]  # non-empty


@pytest.mark.asyncio
async def test_list_suite_runs(client, auth_headers_get, monkeypatch):
    from app.suites import Suite, SuitePrompt

    monkeypatch.setattr(
        runtime,
        "suites",
        {"foo": Suite(name="foo", description="", category="general", prompts=[SuitePrompt(id="p", prompt="q")])},
    )
    # No runs yet
    resp = await client.get("/api/suites/foo/runs", headers=auth_headers_get)
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_suite_runs_404_unknown_run(client, auth_headers_get):
    resp = await client.get("/api/suites/runs/abcdefghij123456", headers=auth_headers_get)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_judge_endpoint_400_when_responses_missing(client, auth_headers, monkeypatch):
    from app import main
    from app.config import Judge, Model

    judge_model = Model(
        id="fake-judge-3",
        provider_name=next(iter(main.config.providers)),
        display_name="Fake",
        model_id="fake",
        input_cost_per_1m=0.0,
        output_cost_per_1m=0.0,
    )
    monkeypatch.setattr(main.config, "models", main.config.models + [judge_model], raising=False)
    monkeypatch.setattr(main.config, "judge", Judge(model_id="fake-judge-3"), raising=False)

    create_resp = await client.post(
        "/api/battle",
        json={"prompt": "q", "category": "general"},
        headers=auth_headers,
    )
    battle_id = create_resp.json()["battle_id"]
    # Do NOT populate responses

    resp = await client.post(f"/api/battle/{battle_id}/judge", headers=auth_headers)
    assert resp.status_code == 400
    assert "both responses" in resp.json()["detail"]


# --- Metrics + cost dashboard ---


@pytest.mark.asyncio
async def test_metrics_endpoint_returns_prometheus_format(client, auth_headers_get):
    resp = await client.get("/api/metrics", headers=auth_headers_get)
    assert resp.status_code == 200
    body = resp.text
    # Prometheus text format — at least the well-known counters we defined
    assert "arena_battles_started_total" in body
    assert "arena_votes_total" in body
    assert "arena_model_cost_dollars_total" in body


@pytest.mark.asyncio
async def test_metrics_endpoint_requires_auth(client):
    resp = await client.get("/api/metrics", follow_redirects=False)
    # Cookie auth would redirect; API auth returns 401. Either is "denied".
    assert resp.status_code in (307, 401)


@pytest.mark.asyncio
async def test_costs_endpoint_empty(client, auth_headers_get):
    resp = await client.get("/api/costs", headers=auth_headers_get)
    assert resp.status_code == 200
    data = resp.json()
    assert data["window_days"] == 30
    assert data["total_cost"] == 0
    assert data["per_model"] == []


@pytest.mark.asyncio
async def test_costs_endpoint_after_battle(client, auth_headers, auth_headers_get):
    """Costs aggregate per model with measured per-1k-token normalization."""
    create_resp = await client.post(
        "/api/battle",
        json={"prompt": "q", "category": "general"},
        headers=auth_headers,
    )
    battle_id = create_resp.json()["battle_id"]
    await store.update_response_a(battle_id, "a", 300, 200, 0.01)
    await store.update_response_b(battle_id, "b", 400, 400, 0.02)
    # Vote so both battles/logs exist for the timeframe
    await client.post(f"/api/battle/{battle_id}/vote", json={"winner": "a"}, headers=auth_headers)

    resp = await client.get("/api/costs?days=7", headers=auth_headers_get)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_cost"] == pytest.approx(0.03, abs=1e-6)
    # Two distinct models each with battles
    assert len(data["per_model"]) == 2
    for row in data["per_model"]:
        assert "measured_cost_per_1k_output_tokens" in row
        assert "share_pct" in row
        assert row["battles"] == 1


@pytest.mark.asyncio
async def test_costs_rejects_bad_window(client, auth_headers_get):
    resp = await client.get("/api/costs?days=0", headers=auth_headers_get)
    assert resp.status_code == 400
    resp = await client.get("/api/costs?days=100000", headers=auth_headers_get)
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_metrics_records_battle_created(client, auth_headers, auth_headers_get):
    """Creating a battle bumps the arena_battles_started_total counter."""
    # Grab the current value first
    before_resp = await client.get("/api/metrics", headers=auth_headers_get)
    before = before_resp.text

    await client.post(
        "/api/battle",
        json={"prompt": "metric-check", "category": "general"},
        headers=auth_headers,
    )

    after_resp = await client.get("/api/metrics", headers=auth_headers_get)
    after = after_resp.text

    # The general counter must exist in "after" and have a strictly larger value
    def _counter_val(text: str, name: str) -> float:
        for line in text.splitlines():
            if line.startswith(name) and not line.startswith("#"):
                return float(line.rsplit(" ", 1)[1])
        return 0.0

    label = 'arena_battles_started_total{category="general"}'
    assert _counter_val(after, label) > _counter_val(before, label)


# --- Thinking / reasoning effort ---


@pytest.mark.asyncio
async def test_create_battle_stores_reasoning_effort(client, auth_headers):
    resp = await client.post(
        "/api/battle",
        json={"prompt": "think hard", "category": "general", "reasoning_effort": "high"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["reasoning_effort"] == "high"
    battle = await store.get_battle(resp.json()["battle_id"])
    assert battle["reasoning_effort"] == "high"


@pytest.mark.asyncio
async def test_create_battle_reasoning_off_is_null(client, auth_headers):
    resp = await client.post(
        "/api/battle",
        json={"prompt": "hi", "category": "general", "reasoning_effort": "off"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["reasoning_effort"] is None


@pytest.mark.asyncio
async def test_create_battle_rejects_bad_reasoning_effort(client, auth_headers):
    resp = await client.post(
        "/api/battle",
        json={"prompt": "hi", "category": "general", "reasoning_effort": "ultra"},
        headers=auth_headers,
    )
    assert resp.status_code == 400
    assert "reasoning_effort" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_models_and_features_advertise_reasoning(client, auth_headers_get):
    resp = await client.get("/api/models", headers=auth_headers_get)
    assert resp.status_code == 200
    assert all(m["reasoning"] in ("auto", "yes", "off") for m in resp.json())
    resp = await client.get("/api/features", headers=auth_headers_get)
    assert resp.json()["reasoning"]["efforts"] == ["low", "medium", "high"]
    assert resp.json()["audience"]["enabled"] is True


# --- Single explicit model, random opponent ---


@pytest.mark.asyncio
async def test_create_battle_one_model_picks_random_opponent(client, auth_headers):
    from app import main

    ids = {m.id for m in main.config.enabled_models("general")}
    chosen = sorted(ids)[0]
    resp = await client.post(
        "/api/battle",
        json={"prompt": "hi", "category": "general", "model_a": chosen},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    battle = await store.get_battle(resp.json()["battle_id"])
    assert battle["model_a"] == chosen
    assert battle["model_b"] != chosen
    assert battle["model_b"] in ids

    resp = await client.post(
        "/api/battle",
        json={"prompt": "hi", "category": "general", "model_b": chosen},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    battle = await store.get_battle(resp.json()["battle_id"])
    assert battle["model_b"] == chosen
    assert battle["model_a"] != chosen


@pytest.mark.asyncio
async def test_create_battle_one_model_unknown(client, auth_headers):
    resp = await client.post(
        "/api/battle",
        json={"prompt": "hi", "category": "general", "model_b": "ghost"},
        headers=auth_headers,
    )
    assert resp.status_code == 400


# --- Audience polls ---


async def _finished_battle(client, auth_headers) -> str:
    resp = await client.post("/api/battle", json={"prompt": "pick one", "category": "general"}, headers=auth_headers)
    battle_id = resp.json()["battle_id"]
    await store.update_response_a(battle_id, "Answer from A", 100, 10, 0.001)
    await store.update_response_b(battle_id, "Answer from B", 120, 12, 0.002)
    return battle_id


def test_majority_rules():
    from app.routes_polls import majority

    assert majority({"a": 3, "b": 1, "tie": 0}) == "a"
    assert majority({"a": 1, "b": 4, "tie": 2}) == "b"
    assert majority({"a": 2, "b": 2, "tie": 0}) == "tie"
    assert majority({"a": 1, "b": 1, "tie": 5}) == "tie"
    assert majority({"a": 0, "b": 0, "tie": 0}) == "tie"


@pytest.mark.asyncio
async def test_poll_end_to_end(client, auth_headers, auth_headers_get):
    battle_id = await _finished_battle(client, auth_headers)

    # Presenter opens the poll (auth required)
    resp = await client.post(f"/api/battle/{battle_id}/poll")
    assert resp.status_code == 401
    resp = await client.post(f"/api/battle/{battle_id}/poll", headers=auth_headers)
    assert resp.status_code == 200
    poll = resp.json()
    code = poll["code"]
    assert poll["status"] == "open"
    assert poll["join_path"] == f"/vote/{code}"
    assert poll["tally"]["total"] == 0

    # Opening again returns the same code
    resp = await client.post(f"/api/battle/{battle_id}/poll", headers=auth_headers)
    assert resp.json()["code"] == code

    # Phones: no auth, no CSRF. Page and poll state are public.
    resp = await client.get(f"/vote/{code}")
    assert resp.status_code == 200
    assert b"audience" in resp.content.lower()
    resp = await client.get(f"/api/audience/{code.lower()}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "open"
    assert body["response_a"] == "Answer from A"
    assert "tally" not in body  # hidden while open
    assert "model_a_name" not in body

    # Three phones vote; one changes its mind
    for voter, choice in (("phone-aaaa-0001", "a"), ("phone-bbbb-0002", "b"), ("phone-cccc-0003", "b")):
        resp = await client.post(f"/api/audience/{code}/vote", json={"voter_id": voter, "choice": choice})
        assert resp.status_code == 200, resp.text
    resp = await client.post(f"/api/audience/{code}/vote", json={"voter_id": "phone-aaaa-0001", "choice": "b"})
    assert resp.json()["vote_count"] == 3
    resp = await client.get(f"/api/audience/{code}?voter_id=phone-aaaa-0001")
    assert resp.json()["your_choice"] == "b"
    assert resp.json()["vote_count"] == 3

    # Bad input on the public side
    resp = await client.post(f"/api/audience/{code}/vote", json={"voter_id": "x", "choice": "a"})
    assert resp.status_code == 400
    resp = await client.post(f"/api/audience/{code}/vote", json={"voter_id": "phone-dddd-0004", "choice": "z"})
    assert resp.status_code == 400
    resp = await client.post("/api/audience/ZZZZZZ/vote", json={"voter_id": "phone-dddd-0004", "choice": "a"})
    assert resp.status_code == 404
    resp = await client.get("/api/audience/not-a-code")
    assert resp.status_code == 400

    # Presenter sees the live tally
    resp = await client.get(f"/api/battle/{battle_id}/poll", headers=auth_headers_get)
    assert resp.json()["tally"] == {"a": 0, "b": 3, "tie": 0, "total": 3}

    # Close: plurality becomes the recorded vote
    resp = await client.post(f"/api/battle/{battle_id}/poll/close", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    reveal = resp.json()
    assert reveal["winner"] == "b"
    assert reveal["vote_method"] == "audience"
    assert reveal["audience_tally"]["total"] == 3
    assert reveal["rating_b_after"] > reveal["rating_b_before"]
    assert "model_a_name" in reveal

    # After close: phones see the reveal, further votes are refused
    resp = await client.get(f"/api/audience/{code}")
    body = resp.json()
    assert body["status"] == "closed"
    assert body["winner"] == "b"
    assert body["tally"]["b"] == 3
    assert body["model_a_name"] and body["model_b_name"]
    resp = await client.post(f"/api/audience/{code}/vote", json={"voter_id": "phone-eeee-0005", "choice": "a"})
    assert resp.status_code == 409
    resp = await client.post(f"/api/battle/{battle_id}/poll/close", headers=auth_headers)
    assert resp.status_code == 409

    # Permalink carries the tally
    resp = await client.get(f"/api/battle/{battle_id}", headers=auth_headers_get)
    assert resp.json()["vote_method"] == "audience"
    assert resp.json()["audience_tally"] == {"a": 0, "b": 3, "tie": 0, "total": 3}


@pytest.mark.asyncio
async def test_poll_open_requires_finished_unvoted_battle(client, auth_headers, auth_headers_get):
    resp = await client.post("/api/battle", json={"prompt": "x", "category": "general"}, headers=auth_headers)
    battle_id = resp.json()["battle_id"]
    resp = await client.post(f"/api/battle/{battle_id}/poll", headers=auth_headers)
    assert resp.status_code == 400  # responses not in yet

    battle_id = await _finished_battle(client, auth_headers)
    await client.post(f"/api/battle/{battle_id}/vote", json={"winner": "a"}, headers=auth_headers)
    resp = await client.post(f"/api/battle/{battle_id}/poll", headers=auth_headers)
    assert resp.status_code == 409

    resp = await client.get("/api/battle/abcdefghij123456/poll", headers=auth_headers_get)
    assert resp.status_code == 404
    resp = await client.post("/api/battle/abcdefghij123456/poll", headers=auth_headers)
    assert resp.status_code == 404
    resp = await client.post("/api/battle/bad!/poll", headers=auth_headers)
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_poll_close_needs_votes_and_loses_to_manual_vote(client, auth_headers):
    battle_id = await _finished_battle(client, auth_headers)
    await client.post(f"/api/battle/{battle_id}/poll", headers=auth_headers)
    resp = await client.post(f"/api/battle/{battle_id}/poll/close", headers=auth_headers)
    assert resp.status_code == 400  # nothing to count

    code = (await client.get(f"/api/battle/{battle_id}/poll", headers=auth_headers)).json()["code"]
    await client.post(f"/api/audience/{code}/vote", json={"voter_id": "phone-aaaa-0001", "choice": "a"})
    # Presenter votes by hand first; closing the poll must not double-vote
    await client.post(f"/api/battle/{battle_id}/vote", json={"winner": "tie"}, headers=auth_headers)
    resp = await client.post(f"/api/battle/{battle_id}/poll/close", headers=auth_headers)
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_audience_vote_rate_limited(client, auth_headers):
    battle_id = await _finished_battle(client, auth_headers)
    code = (await client.post(f"/api/battle/{battle_id}/poll", headers=auth_headers)).json()["code"]
    runtime.audience_limiter.requests.clear()
    limit = runtime.audience_limiter.max_requests
    for i in range(limit):
        resp = await client.post(f"/api/audience/{code}/vote", json={"voter_id": f"phone-{i:010d}", "choice": "a"})
        assert resp.status_code == 200
    resp = await client.post(f"/api/audience/{code}/vote", json={"voter_id": "phone-overflow-1", "choice": "a"})
    assert resp.status_code == 429
    runtime.audience_limiter.requests.clear()


@pytest.mark.asyncio
async def test_audience_prefix_never_reaches_battle_creation(client):
    """The public prefix must not open a path to anything that spends money."""
    resp = await client.post("/api/audience/ABCDEF/vote/../../battle", json={"prompt": "x"})
    assert resp.status_code in (400, 401, 404, 405, 422)
    resp = await client.post("/api/battle", json={"prompt": "x", "category": "general"})
    assert resp.status_code == 401
