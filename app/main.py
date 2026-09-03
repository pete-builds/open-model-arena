from __future__ import annotations

import csv
import hmac
import io
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

from . import routes_polls, routes_suites
from .arena import normalize_reasoning_effort, pick_opponent, select_models, stream_battle
from .auth import bearer_from_request_headers, bearer_matches, load_api_tokens, make_token
from .clientip import TRUSTED_PROXIES, get_client_ip  # noqa: F401  (re-exported for operators + tests)
from .config import REASONING_EFFORTS
from .judge import JudgeError, run_judge
from .metrics import record_battle_started, record_vote
from .models import BattleRequest, VoteRequest
from .payloads import parse_tally, reveal_payload
from .runtime import battle_limiter, config, store, suites  # noqa: F401  (shared singletons)

logging.basicConfig(
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=logging.INFO,
)
log = logging.getLogger("arena")

# Auth config — refuse to start with defaults
ARENA_PASSPHRASE = os.environ.get("ARENA_PASSPHRASE", "")
AUTH_TOKEN_SECRET = os.environ.get("AUTH_TOKEN_SECRET", "")

if not ARENA_PASSPHRASE or not AUTH_TOKEN_SECRET:
    raise SystemExit(
        "FATAL: ARENA_PASSPHRASE and AUTH_TOKEN_SECRET must be set in environment. See .env.example for details."
    )

# Cookies carry the Secure flag by default, which is right behind any TLS
# terminator (Caddy, nginx, a Tailscale Funnel). On a plain-HTTP LAN port the
# browser then drops the cookie and login silently loops, so operators can
# turn the flag off with COOKIE_SECURE=false. Never do that on a public host.
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "true").strip().lower() not in ("0", "false", "no", "off")
if not COOKIE_SECURE:
    log.warning("COOKIE_SECURE=false: auth cookies will be sent over plain HTTP")


def _make_token(passphrase: str) -> str:
    """Create an HMAC token from the passphrase."""
    return make_token(passphrase, AUTH_TOKEN_SECRET)


# Bearer tokens for headless / CI use — see app/auth.py for details.
API_TOKENS = load_api_tokens()


# Paths that don't require auth
PUBLIC_PATHS = {"/login", "/api/login", "/login.html", "/healthz"}
# Audience-side poll surface: students on their phones never hold the
# passphrase. Nothing under these prefixes can create a battle or reach a
# model provider — see app/routes_polls.py.
PUBLIC_PREFIXES = ("/api/audience/", "/vote/")


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Allow public paths and static assets for login page
        if (
            path in PUBLIC_PATHS
            or path.startswith(PUBLIC_PREFIXES)
            or path.endswith((".css", ".js", ".woff2", ".ico", ".svg"))
        ):
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
        secure=COOKIE_SECURE,
        samesite="lax",
    )
    response.set_cookie(
        key="arena_csrf",
        value=csrf_token,
        max_age=7 * 24 * 3600,
        httponly=False,  # JS needs to read this
        secure=COOKIE_SECURE,
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


# Kept as the historical name; the implementation lives in app/clientip.py.
_get_client_ip = get_client_ip


@app.post("/api/battle")
async def create_battle(req: BattleRequest, request: Request):
    client_ip = get_client_ip(request)
    if not battle_limiter.is_allowed(client_ip):
        raise HTTPException(429, "slow down — max 10 battles per minute")

    if not req.prompt.strip():
        raise HTTPException(400, "prompt is required")
    if len(req.prompt) > 10000:
        raise HTTPException(400, "prompt too long (max 10000 chars)")

    try:
        reasoning_effort = normalize_reasoning_effort(req.reasoning_effort)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

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

    def _explicit(model_id: str):
        model = config.get_model(model_id)
        if not model or not model.enabled:
            raise HTTPException(400, f"model not found: {model_id}")
        # An explicit model must actually support the requested category —
        # otherwise Elo ratings for that category get updated for models that
        # never competed in it.
        if req.category not in model.categories:
            raise HTTPException(400, f"model '{model.id}' does not support category '{req.category}'")
        return model

    try:
        if req.model_a and req.model_b:
            if req.model_a == req.model_b:
                raise HTTPException(400, "pick two different models")
            model_a = _explicit(req.model_a)
            model_b = _explicit(req.model_b)
        elif req.model_a:
            # One side chosen, the other drawn at random. The chosen model
            # keeps the slot the user put it in.
            model_a = _explicit(req.model_a)
            model_b = pick_opponent(config, req.category, model_a)
        elif req.model_b:
            model_b = _explicit(req.model_b)
            model_a = pick_opponent(config, req.category, model_b)
        else:
            model_a, model_b = select_models(config, req.category)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

    battle_id = await store.create_battle(req.prompt, req.category, model_a.id, model_b.id, reasoning_effort)
    record_battle_started(req.category)
    return {"battle_id": battle_id, "reasoning_effort": reasoning_effort}


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
    return reveal_payload(config, battle, **elo_results)


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

    elo = await store.get_vote_log(battle_id)

    return reveal_payload(
        config,
        battle,
        id=battle["id"],
        prompt=battle["prompt"],
        category=battle["category"],
        response_a=battle["response_a"],
        response_b=battle["response_b"],
        winner=battle["winner"],
        created_at=battle["created_at"],
        voted_at=battle["voted_at"],
        rating_a_before=elo["rating_a_before"] if elo else None,
        rating_b_before=elo["rating_b_before"] if elo else None,
        rating_a_after=elo["rating_a_after"] if elo else None,
        rating_b_after=elo["rating_b_after"] if elo else None,
        vote_method=elo["method"] if elo else None,
        judge_reasoning=elo["judge_reasoning"] if elo else None,
        judge_model_id=elo["judge_model_id"] if elo else None,
        judge_cost=elo["judge_cost"] if elo else None,
        audience_tally=parse_tally(elo["audience_tally"]) if elo else None,
    )


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

    return reveal_payload(
        config,
        battle,
        vote_method="judge",
        judge_reasoning=verdict["reasoning"],
        judge_model_id=verdict["judge_model_id"],
        judge_display_name=verdict["judge_display_name"],
        judge_cost=verdict["cost"],
        judge_latency_ms=verdict["latency_ms"],
        **elo_results,
    )


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
    return [
        {
            "id": m.id,
            "display_name": m.display_name,
            "categories": m.categories,
            "provider": m.provider_name,
            "reasoning": "off" if m.reasoning is False else ("yes" if m.reasoning else "auto"),
        }
        for m in config.enabled_models()
    ]


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
        "reasoning": {
            "efforts": list(REASONING_EFFORTS),
        },
        "audience": {
            "enabled": True,
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
            "reasoning_effort",
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


# --- Routers ---

app.include_router(routes_suites.router)
app.include_router(routes_polls.router)


# --- Static Files + SPA Routing ---


@app.get("/battle/{battle_id}")
async def battle_page(battle_id: str):
    _validate_battle_id(battle_id)
    return FileResponse("static/index.html")


@app.get("/leaderboard")
async def leaderboard_page():
    return FileResponse("static/index.html")


app.mount("/", StaticFiles(directory="static", html=True), name="static")
