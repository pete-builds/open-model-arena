from __future__ import annotations

import asyncio
import csv
import hmac
import io
import ipaddress
import logging
import os
import re
import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel as PydanticBaseModel
from starlette.middleware.base import BaseHTTPMiddleware

from .arena import estimate_cost, get_client, run_battle_headless, select_models, stream_battle  # noqa: F401
from .auth import bearer_from_request_headers, bearer_matches, load_api_tokens, make_token
from .config import load_config
from .judge import JudgeError, run_judge
from .metrics import (
    record_battle_started,
    record_suite_run_completed,
    record_suite_run_started,
    record_vote,
)
from .models import BattleRequest, VoteRequest
from .ratelimit import RateLimiter
from .store import Store
from .suites import load_suites

logging.basicConfig(
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=logging.INFO,
)
log = logging.getLogger("arena")

config = load_config()
suites = load_suites()
store = Store()
battle_limiter = RateLimiter(max_requests=10, window_seconds=60)

# Auth config — refuse to start with defaults
ARENA_PASSPHRASE = os.environ.get("ARENA_PASSPHRASE", "")
AUTH_TOKEN_SECRET = os.environ.get("AUTH_TOKEN_SECRET", "")

if not ARENA_PASSPHRASE or not AUTH_TOKEN_SECRET:
    raise SystemExit(
        "FATAL: ARENA_PASSPHRASE and AUTH_TOKEN_SECRET must be set in environment. See .env.example for details."
    )


def _make_token(passphrase: str) -> str:
    """Create an HMAC token from the passphrase."""
    return make_token(passphrase, AUTH_TOKEN_SECRET)


# Bearer tokens for headless / CI use — see app/auth.py for details.
API_TOKENS = load_api_tokens()


def _parse_trusted_proxies(raw: str) -> list[ipaddress._BaseNetwork]:
    """Parse a comma-separated list of IPs / CIDR blocks into networks.

    Bare IPs are treated as /32 (IPv4) or /128 (IPv6). Malformed entries are
    skipped with a warning so a fat-fingered value doesn't crash boot; the
    net effect of a skipped entry is that the peer won't be trusted and its
    X-Forwarded-For header will be ignored, which is the safe default.
    """
    networks: list[ipaddress._BaseNetwork] = []
    for token in (t.strip() for t in raw.split(",")):
        if not token:
            continue
        try:
            networks.append(ipaddress.ip_network(token, strict=False))
        except ValueError as e:
            log.warning("ignoring malformed TRUSTED_PROXIES entry %r: %s", token, e)
    return networks


# Reverse proxies whose X-Forwarded-For headers we're willing to trust.
# Empty (default) means: NEVER honor XFF — every request keys off its
# socket peer, so an untrusted client can't spoof its way past the battle
# rate limit. Operators fronting the app with Caddy / nginx / Tailscale
# Funnel should list the proxy's egress IPs or CIDRs here.
TRUSTED_PROXIES = _parse_trusted_proxies(os.environ.get("TRUSTED_PROXIES", ""))
if not TRUSTED_PROXIES:
    log.info("TRUSTED_PROXIES not set; X-Forwarded-For headers will be ignored")


# Paths that don't require auth
PUBLIC_PATHS = {"/login", "/api/login", "/login.html", "/healthz"}


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Allow public paths and static assets for login page
        if path in PUBLIC_PATHS or path.endswith((".css", ".js", ".woff2", ".ico", ".svg")):
            return await call_next(request)

        # Bearer-token path: headless / CI clients scoped to /api/*. Bearer
        # auth skips CSRF because it isn't carried on cross-site navigations.
        if API_TOKENS and path.startswith("/api/"):
            bearer = bearer_from_request_headers(
                request.headers.get("authorization"),
                request.headers.get("x-api-token"),
            )
            if bearer and bearer_matches(bearer, API_TOKENS):
                request.state.auth_method = "bearer"
                return await call_next(request)

        # Cookie path: browser session, gated by passphrase + CSRF for POST.
        token = request.cookies.get("arena_token")
        expected = _make_token(ARENA_PASSPHRASE)
        if token and hmac.compare_digest(token, expected):
            # CSRF check on POST requests (double-submit cookie pattern)
            if request.method == "POST":
                csrf_cookie = request.cookies.get("arena_csrf")
                csrf_header = request.headers.get("x-csrf-token")
                if not csrf_cookie or not csrf_header or not hmac.compare_digest(csrf_cookie, csrf_header):
                    return JSONResponse({"detail": "CSRF validation failed"}, status_code=403)
            request.state.auth_method = "cookie"
            return await call_next(request)

        # Not authenticated — redirect HTML requests, 401 API requests
        if path.startswith("/api/"):
            return JSONResponse({"detail": "not authenticated"}, status_code=401)
        return RedirectResponse("/login")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await store.connect()
    yield
    await store.close()


app = FastAPI(lifespan=lifespan)
app.add_middleware(AuthMiddleware)


# --- Auth Routes ---


class LoginRequest(PydanticBaseModel):
    passphrase: str


@app.get("/login")
async def login_page():
    return FileResponse("static/login.html")


@app.post("/api/login")
async def login(req: LoginRequest):
    if not hmac.compare_digest(req.passphrase, ARENA_PASSPHRASE):
        raise HTTPException(401, "invalid passphrase")

    token = _make_token(ARENA_PASSPHRASE)
    csrf_token = secrets.token_hex(32)
    response = JSONResponse({"ok": True})
    response.set_cookie(
        key="arena_token",
        value=token,
        max_age=7 * 24 * 3600,  # 7 days
        httponly=True,
        secure=True,
        samesite="lax",
    )
    response.set_cookie(
        key="arena_csrf",
        value=csrf_token,
        max_age=7 * 24 * 3600,
        httponly=False,  # JS needs to read this
        secure=True,
        samesite="lax",
    )
    return response


# --- Health Check ---


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


# --- API Routes ---

_BATTLE_ID_RE = re.compile(r"^[a-zA-Z0-9]{16}$")


def _validate_battle_id(battle_id: str) -> None:
    """Reject battle IDs that don't match the expected format."""
    if not _BATTLE_ID_RE.match(battle_id):
        raise HTTPException(400, "invalid battle ID format")


def _get_client_ip(request: Request) -> str:
    """Return the client IP to key rate-limits on.

    X-Forwarded-For is only honored when the direct peer is in TRUSTED_PROXIES.
    Otherwise a client on the open internet could rotate the header to bypass
    the per-IP battle rate limit — the direct-port deployment shipped with no
    reverse proxy at all, and the limiter was the only thing between an
    authenticated caller and unbounded provider spend.
    """
    peer_host = request.client.host if request.client else None

    if peer_host and TRUSTED_PROXIES:
        try:
            peer_ip = ipaddress.ip_address(peer_host)
            if any(peer_ip in net for net in TRUSTED_PROXIES):
                forwarded = request.headers.get("x-forwarded-for")
                if forwarded:
                    # The leftmost IP is the original client; everything after
                    # is the proxy chain we just verified.
                    return forwarded.split(",")[0].strip()
        except ValueError:
            # Peer host wasn't an IP (unix socket etc.); fall through and key
            # on the raw peer string below.
            pass

    return peer_host or "unknown"


@app.post("/api/battle")
async def create_battle(req: BattleRequest, request: Request):
    client_ip = _get_client_ip(request)
    if not battle_limiter.is_allowed(client_ip):
        raise HTTPException(429, "slow down — max 10 battles per minute")

    if not req.prompt.strip():
        raise HTTPException(400, "prompt is required")
    if len(req.prompt) > 10000:
        raise HTTPException(400, "prompt too long (max 10000 chars)")

    # Reject caller-controlled categories that no enabled model advertises.
    # The auto-select path already fails on unknown categories via
    # select_models, but the explicit-models path below skips that check —
    # so a request could otherwise mint a battle in an arbitrary category
    # string and pollute the ratings table with a spurious bucket.
    known = config.known_categories()
    if req.category not in known:
        raise HTTPException(
            400,
            f"unknown category '{req.category}'; must be one of: {', '.join(sorted(known)) or '(none configured)'}",
        )

    if req.model_a and req.model_b:
        model_a = config.get_model(req.model_a)
        model_b = config.get_model(req.model_b)
        if not model_a:
            raise HTTPException(400, f"model not found: {req.model_a}")
        if not model_b:
            raise HTTPException(400, f"model not found: {req.model_b}")
        if req.model_a == req.model_b:
            raise HTTPException(400, "pick two different models")
        # Both explicit models must actually support the requested category —
        # otherwise Elo ratings for that category get updated for models that
        # never competed in it.
        for m in (model_a, model_b):
            if req.category not in m.categories:
                raise HTTPException(400, f"model '{m.id}' does not support category '{req.category}'")
    else:
        try:
            model_a, model_b = select_models(config, req.category)
        except ValueError as e:
            raise HTTPException(400, str(e))

    battle_id = await store.create_battle(req.prompt, req.category, model_a.id, model_b.id)
    record_battle_started(req.category)
    return {"battle_id": battle_id}


@app.get("/api/battle/{battle_id}/stream")
async def stream(battle_id: str):
    _validate_battle_id(battle_id)
    battle = await store.get_battle(battle_id)
    if not battle:
        raise HTTPException(404, "battle not found")

    return StreamingResponse(
        stream_battle(config, store, battle_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/battle/{battle_id}/vote")
async def vote(battle_id: str, req: VoteRequest):
    _validate_battle_id(battle_id)
    if req.winner not in ("a", "b", "tie"):
        raise HTTPException(400, "winner must be 'a', 'b', or 'tie'")

    battle = await store.get_battle(battle_id)
    if not battle:
        raise HTTPException(404, "battle not found")

    try:
        elo_results = await store.record_vote(battle_id, req.winner)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

    record_vote("human", req.winner)

    # Reveal model identities
    model_a = config.get_model(battle["model_a"])
    model_b = config.get_model(battle["model_b"])

    return {
        "model_a_id": battle["model_a"],
        "model_a_name": model_a.display_name if model_a else battle["model_a"],
        "model_a_provider": model_a.provider_name if model_a else "unknown",
        "model_b_id": battle["model_b"],
        "model_b_name": model_b.display_name if model_b else battle["model_b"],
        "model_b_provider": model_b.provider_name if model_b else "unknown",
        "latency_a_ms": battle["latency_a_ms"],
        "latency_b_ms": battle["latency_b_ms"],
        "tokens_a": battle["tokens_a"],
        "tokens_b": battle["tokens_b"],
        "cost_a": battle["cost_a"],
        "cost_b": battle["cost_b"],
        **elo_results,
    }


@app.get("/api/battle/{battle_id}")
async def get_battle(battle_id: str):
    """Return the full completed-battle state for a permalink view.

    Only voted battles are returned; in-flight and abandoned battles are 404
    so a share link never leaks a mid-stream state.
    """
    _validate_battle_id(battle_id)
    battle = await store.get_battle(battle_id)
    if not battle or not battle.get("winner"):
        raise HTTPException(404, "battle not found")

    model_a = config.get_model(battle["model_a"])
    model_b = config.get_model(battle["model_b"])
    elo = await store.get_vote_log(battle_id)

    return {
        "id": battle["id"],
        "prompt": battle["prompt"],
        "category": battle["category"],
        "response_a": battle["response_a"],
        "response_b": battle["response_b"],
        "winner": battle["winner"],
        "created_at": battle["created_at"],
        "voted_at": battle["voted_at"],
        "model_a_id": battle["model_a"],
        "model_a_name": model_a.display_name if model_a else battle["model_a"],
        "model_a_provider": model_a.provider_name if model_a else "unknown",
        "model_b_id": battle["model_b"],
        "model_b_name": model_b.display_name if model_b else battle["model_b"],
        "model_b_provider": model_b.provider_name if model_b else "unknown",
        "latency_a_ms": battle["latency_a_ms"],
        "latency_b_ms": battle["latency_b_ms"],
        "tokens_a": battle["tokens_a"],
        "tokens_b": battle["tokens_b"],
        "cost_a": battle["cost_a"],
        "cost_b": battle["cost_b"],
        "rating_a_before": elo["rating_a_before"] if elo else None,
        "rating_b_before": elo["rating_b_before"] if elo else None,
        "rating_a_after": elo["rating_a_after"] if elo else None,
        "rating_b_after": elo["rating_b_after"] if elo else None,
        "vote_method": elo["method"] if elo else None,
        "judge_reasoning": elo["judge_reasoning"] if elo else None,
        "judge_model_id": elo["judge_model_id"] if elo else None,
        "judge_cost": elo["judge_cost"] if elo else None,
    }


@app.post("/api/battle/{battle_id}/judge")
async def judge_battle(battle_id: str):
    """Have the configured judge model decide the winner and cast a vote.

    Requires ``judge:`` to be set in models.yaml. Returns the same payload as
    ``POST .../vote`` plus the judge's reasoning and cost. The judge cannot
    vote if either response is missing/errored — the caller should fall back
    to a human vote in that case.
    """
    _validate_battle_id(battle_id)
    if not config.judge:
        raise HTTPException(400, "judge not configured; add a 'judge:' section to models.yaml")
    judge_model = config.judge_model()
    if not judge_model:
        raise HTTPException(500, f"judge model '{config.judge.model_id}' not found in config")

    battle = await store.get_battle(battle_id)
    if not battle:
        raise HTTPException(404, "battle not found")
    if battle.get("winner"):
        raise HTTPException(409, "battle already voted")
    if not battle.get("response_a") or not battle.get("response_b"):
        raise HTTPException(400, "both responses must complete before judging")

    try:
        verdict = await run_judge(
            config,
            config.judge,
            judge_model,
            battle["prompt"],
            battle["response_a"],
            battle["response_b"],
        )
    except JudgeError as e:
        raise HTTPException(502, f"judge failed: {e}") from e

    try:
        elo_results = await store.record_vote(
            battle_id,
            verdict["winner"],
            method="judge",
            judge_reasoning=verdict["reasoning"],
            judge_model_id=verdict["judge_model_id"],
            judge_cost=verdict["cost"],
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

    record_vote("judge", verdict["winner"], judge_model_id=verdict["judge_model_id"], judge_cost=verdict["cost"])

    model_a = config.get_model(battle["model_a"])
    model_b = config.get_model(battle["model_b"])
    return {
        "model_a_id": battle["model_a"],
        "model_a_name": model_a.display_name if model_a else battle["model_a"],
        "model_a_provider": model_a.provider_name if model_a else "unknown",
        "model_b_id": battle["model_b"],
        "model_b_name": model_b.display_name if model_b else battle["model_b"],
        "model_b_provider": model_b.provider_name if model_b else "unknown",
        "latency_a_ms": battle["latency_a_ms"],
        "latency_b_ms": battle["latency_b_ms"],
        "tokens_a": battle["tokens_a"],
        "tokens_b": battle["tokens_b"],
        "cost_a": battle["cost_a"],
        "cost_b": battle["cost_b"],
        "vote_method": "judge",
        "judge_reasoning": verdict["reasoning"],
        "judge_model_id": verdict["judge_model_id"],
        "judge_display_name": verdict["judge_display_name"],
        "judge_cost": verdict["cost"],
        "judge_latency_ms": verdict["latency_ms"],
        **elo_results,
    }


MIN_BATTLES_FOR_RANKING = 5


@app.get("/api/leaderboard")
async def leaderboard(category: str = "overall"):
    rows = await store.get_leaderboard(category)

    # Split into ranked and provisional
    ranked_rows = []
    provisional_rows = []
    for row in rows:
        total = row["wins"] + row["losses"] + row["ties"]
        if total >= MIN_BATTLES_FOR_RANKING:
            ranked_rows.append(row)
        else:
            provisional_rows.append(row)

    result = []
    for i, row in enumerate(ranked_rows):
        model = config.get_model(row["model_id"])
        total = row["wins"] + row["losses"] + row["ties"]
        rating = round(row["rating"], 1)

        if i > 0 and rating == result[i - 1]["rating"]:
            rank = result[i - 1]["rank"]
        else:
            rank = i + 1

        result.append(
            {
                "rank": rank,
                "model_id": row["model_id"],
                "display_name": model.display_name if model else row["model_id"],
                "provider": model.provider_name if model else "unknown",
                "rating": rating,
                "wins": row["wins"],
                "losses": row["losses"],
                "ties": row["ties"],
                "win_rate": round(row["wins"] / total * 100, 1) if total > 0 else 0,
                "avg_latency_ms": row.get("avg_latency_ms", 0),
                "provisional": False,
            }
        )

    # Append provisional models (unranked, sorted by rating)
    for row in provisional_rows:
        model = config.get_model(row["model_id"])
        total = row["wins"] + row["losses"] + row["ties"]
        rating = round(row["rating"], 1)
        result.append(
            {
                "rank": None,
                "model_id": row["model_id"],
                "display_name": model.display_name if model else row["model_id"],
                "provider": model.provider_name if model else "unknown",
                "rating": rating,
                "wins": row["wins"],
                "losses": row["losses"],
                "ties": row["ties"],
                "win_rate": round(row["wins"] / total * 100, 1) if total > 0 else 0,
                "avg_latency_ms": row.get("avg_latency_ms", 0),
                "provisional": True,
            }
        )

    return result


@app.get("/api/stats")
async def stats():
    return await store.get_stats()


@app.get("/api/models")
async def list_models():
    return [{"id": m.id, "display_name": m.display_name, "categories": m.categories} for m in config.enabled_models()]


async def _run_suite(run_id: str, suite_name: str) -> None:
    """Background task: run every prompt in a suite, judge, tally.

    Sequential (not parallel) so slow providers don't stampede rate limits.
    Errors on individual prompts are recorded but don't abort the run.
    """
    suite = suites.get(suite_name)
    if not suite:
        await store.finish_suite_run(run_id, "errored", 0.0)
        return

    judge_model = config.judge_model()
    total_cost = 0.0
    status = "completed"

    for prompt in suite.prompts:
        try:
            model_a, model_b = select_models(config, suite.category)
        except ValueError as e:
            await store.record_suite_battle(run_id, prompt.id, None, None, str(e))
            continue

        battle_id = await store.create_battle(prompt.prompt, suite.category, model_a.id, model_b.id)
        try:
            results = await run_battle_headless(config, store, battle_id)
        except Exception as e:
            log.exception("suite %s prompt %s: battle failed", suite_name, prompt.id)
            await store.record_suite_battle(run_id, prompt.id, battle_id, None, f"battle: {e}")
            continue

        err_a = results["a"].get("error")
        err_b = results["b"].get("error")
        if err_a or err_b:
            msg = f"a: {err_a}" if err_a else ""
            msg += (" | " if err_a and err_b else "") + (f"b: {err_b}" if err_b else "")
            await store.record_suite_battle(run_id, prompt.id, battle_id, None, msg)
            continue

        total_cost += results["a"]["cost"] + results["b"]["cost"]

        if not judge_model or not config.judge:
            # No judge → skip the vote, record the battle unfinished. Operator
            # can still vote manually later; the suite run just carries no
            # winner for this prompt.
            await store.record_suite_battle(run_id, prompt.id, battle_id, None, "no judge configured")
            continue

        try:
            verdict = await run_judge(
                config,
                config.judge,
                judge_model,
                prompt.prompt,
                results["a"]["response"],
                results["b"]["response"],
            )
            total_cost += verdict["cost"]
        except JudgeError as e:
            await store.record_suite_battle(run_id, prompt.id, battle_id, None, f"judge: {e}")
            continue

        try:
            await store.record_vote(
                battle_id,
                verdict["winner"],
                method="judge",
                judge_reasoning=verdict["reasoning"],
                judge_model_id=verdict["judge_model_id"],
                judge_cost=verdict["cost"],
            )
        except ValueError as e:
            await store.record_suite_battle(run_id, prompt.id, battle_id, None, f"vote: {e}")
            continue

        await store.record_suite_battle(run_id, prompt.id, battle_id, verdict["winner"], None)

    await store.finish_suite_run(run_id, status, total_cost)
    record_suite_run_completed(suite_name)
    log.info("suite %s run %s done: $%.4f", suite_name, run_id, total_cost)


@app.get("/api/suites")
async def list_suites_route():
    """List all suites the server picked up at startup."""
    return [
        {
            "name": s.name,
            "description": s.description,
            "category": s.category,
            "prompt_count": len(s.prompts),
        }
        for s in suites.values()
    ]


@app.get("/api/suites/{name}")
async def get_suite_route(name: str):
    suite = suites.get(name)
    if not suite:
        raise HTTPException(404, f"suite not found: {name}")
    return {
        "name": suite.name,
        "description": suite.description,
        "category": suite.category,
        "prompts": [{"id": p.id, "prompt": p.prompt} for p in suite.prompts],
    }


@app.post("/api/suites/{name}/run")
async def run_suite_route(name: str):
    """Kick off a background run of the named suite; returns a run_id to poll."""
    suite = suites.get(name)
    if not suite:
        raise HTTPException(404, f"suite not found: {name}")
    if not config.judge_model():
        raise HTTPException(400, "suite runs require a configured judge (see models.yaml)")
    run_id = await store.create_suite_run(name, len(suite.prompts))
    record_suite_run_started(name)
    asyncio.create_task(_run_suite(run_id, name))
    return {"run_id": run_id, "battles_total": len(suite.prompts), "status": "running"}


@app.get("/api/suites/{name}/runs")
async def list_suite_runs_route(name: str):
    if name not in suites:
        raise HTTPException(404, f"suite not found: {name}")
    return await store.list_suite_runs(name)


@app.get("/api/suites/runs/{run_id}")
async def get_suite_run_route(run_id: str):
    run = await store.get_suite_run(run_id)
    if not run:
        raise HTTPException(404, "run not found")
    return run


@app.get("/api/metrics")
async def prometheus_metrics():
    """Prometheus scrape endpoint. Gated by the standard bearer/cookie auth.

    Configure Prometheus with:

        scrape_configs:
          - job_name: model-arena
            metrics_path: /api/metrics
            authorization:
              type: Bearer
              credentials_file: /etc/prometheus/arena-token
            static_configs:
              - targets: ['arena.example:3694']
    """
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/api/costs")
async def cost_dashboard(days: int = 30):
    """Per-model cost breakdown over the last N days.

    Sums `cost_a` and `cost_b` from `battles` (which use real API usage
    numbers when the provider returns them), joins to model display names,
    and derives cost-per-1k-output-tokens per model from measured data —
    the audit-of-record answer to "which model is actually cheapest for us."
    """
    if days < 1 or days > 3650:
        raise HTTPException(400, "days must be between 1 and 3650")

    breakdown = await store.get_cost_breakdown(days)
    total = sum(row["total_cost"] for row in breakdown)
    for row in breakdown:
        model = config.get_model(row["model_id"])
        row["display_name"] = model.display_name if model else row["model_id"]
        row["provider"] = model.provider_name if model else "unknown"
        # Measured cost per 1k output tokens (using real API usage numbers):
        row["measured_cost_per_1k_output_tokens"] = (
            round((row["total_cost"] / row["total_output_tokens"]) * 1000, 6) if row["total_output_tokens"] else None
        )
        row["share_pct"] = round((row["total_cost"] / total) * 100, 1) if total else 0

    return {
        "window_days": days,
        "total_cost": round(total, 4),
        "per_model": sorted(breakdown, key=lambda r: r["total_cost"], reverse=True),
    }


@app.get("/api/features")
async def features():
    """Publish server-side feature flags so the frontend can render conditionally."""
    judge_model = config.judge_model()
    return {
        "judge": {
            "enabled": judge_model is not None,
            "model_id": judge_model.id if judge_model else None,
            "display_name": judge_model.display_name if judge_model else None,
        },
        "suites": {
            "enabled": len(suites) > 0,
            "count": len(suites),
        },
    }


@app.get("/api/export")
async def export_battles(format: str = "csv"):
    if format not in ("csv", "json"):
        raise HTTPException(400, "format must be 'csv' or 'json'")

    battles = await store.get_all_voted_battles()

    # Resolve display names
    for b in battles:
        model_a = config.get_model(b["model_a"])
        model_b = config.get_model(b["model_b"])
        b["model_a_name"] = model_a.display_name if model_a else b["model_a"]
        b["model_b_name"] = model_b.display_name if model_b else b["model_b"]

    if format == "json":
        return JSONResponse(
            content=battles,
            headers={"Content-Disposition": "attachment; filename=open-model-arena-export.json"},
        )

    # CSV
    output = io.StringIO()
    if battles:
        fields = [
            "id",
            "prompt",
            "category",
            "model_a",
            "model_a_name",
            "model_b",
            "model_b_name",
            "winner",
            "latency_a_ms",
            "latency_b_ms",
            "tokens_a",
            "tokens_b",
            "cost_a",
            "cost_b",
            "created_at",
            "voted_at",
        ]
        writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(battles)

    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=open-model-arena-export.csv"},
    )


# --- Static Files + SPA Routing ---


@app.get("/battle/{battle_id}")
async def battle_page(battle_id: str):
    _validate_battle_id(battle_id)
    return FileResponse("static/index.html")


@app.get("/leaderboard")
async def leaderboard_page():
    return FileResponse("static/index.html")


app.mount("/", StaticFiles(directory="static", html=True), name="static")
