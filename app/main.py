from __future__ import annotations

import csv
import hashlib
import hmac
import io
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse, FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel as PydanticBaseModel
from starlette.middleware.base import BaseHTTPMiddleware

from .config import load_config
from .store import Store
from .arena import select_models, stream_battle
from .models import BattleRequest, VoteRequest
from .ratelimit import RateLimiter


config = load_config()
store = Store()
battle_limiter = RateLimiter(max_requests=10, window_seconds=60)

# Auth config — refuse to start with defaults
ARENA_PASSPHRASE = os.environ.get("ARENA_PASSPHRASE", "")
AUTH_TOKEN_SECRET = os.environ.get("AUTH_TOKEN_SECRET", "")

if not ARENA_PASSPHRASE or not AUTH_TOKEN_SECRET:
    raise SystemExit(
        "FATAL: ARENA_PASSPHRASE and AUTH_TOKEN_SECRET must be set in environment. "
        "See .env.example for details."
    )


def _make_token(passphrase: str) -> str:
    """Create an HMAC token from the passphrase."""
    return hmac.new(AUTH_TOKEN_SECRET.encode(), passphrase.encode(), hashlib.sha256).hexdigest()


# Paths that don't require auth
PUBLIC_PATHS = {"/login", "/api/login", "/login.html", "/healthz"}


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Allow public paths and static assets for login page
        if path in PUBLIC_PATHS or path.endswith(('.css', '.js', '.woff2', '.ico', '.svg')):
            return await call_next(request)

        # Check auth cookie
        token = request.cookies.get("arena_token")
        expected = _make_token(ARENA_PASSPHRASE)
        if token and hmac.compare_digest(token, expected):
            # CSRF check on POST requests (double-submit cookie pattern)
            if request.method == "POST":
                csrf_cookie = request.cookies.get("arena_csrf")
                csrf_header = request.headers.get("x-csrf-token")
                if not csrf_cookie or not csrf_header or not hmac.compare_digest(csrf_cookie, csrf_header):
                    return JSONResponse({"detail": "CSRF validation failed"}, status_code=403)
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

def _get_client_ip(request: Request) -> str:
    """Extract real client IP from X-Forwarded-For (set by Tailscale Funnel), fall back to direct connection."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        # Take the first IP (original client), ignore proxies
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@app.post("/api/battle")
async def create_battle(req: BattleRequest, request: Request):
    client_ip = _get_client_ip(request)
    if not battle_limiter.is_allowed(client_ip):
        raise HTTPException(429, "slow down — max 10 battles per minute")

    if not req.prompt.strip():
        raise HTTPException(400, "prompt is required")
    if len(req.prompt) > 10000:
        raise HTTPException(400, "prompt too long (max 10000 chars)")

    if req.model_a and req.model_b:
        model_a = config.get_model(req.model_a)
        model_b = config.get_model(req.model_b)
        if not model_a:
            raise HTTPException(400, f"model not found: {req.model_a}")
        if not model_b:
            raise HTTPException(400, f"model not found: {req.model_b}")
        if req.model_a == req.model_b:
            raise HTTPException(400, "pick two different models")
    else:
        try:
            model_a, model_b = select_models(config, req.category)
        except ValueError as e:
            raise HTTPException(400, str(e))

    battle_id = await store.create_battle(req.prompt, req.category, model_a.id, model_b.id)
    return {"battle_id": battle_id}


@app.get("/api/battle/{battle_id}/stream")
async def stream(battle_id: str):
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
    if req.winner not in ("a", "b", "tie"):
        raise HTTPException(400, "winner must be 'a', 'b', or 'tie'")

    battle = await store.get_battle(battle_id)
    if not battle:
        raise HTTPException(404, "battle not found")

    try:
        elo_results = await store.record_vote(battle_id, req.winner)
    except ValueError as e:
        raise HTTPException(400, str(e))

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

        result.append({
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
        })

    # Append provisional models (unranked, sorted by rating)
    for row in provisional_rows:
        model = config.get_model(row["model_id"])
        total = row["wins"] + row["losses"] + row["ties"]
        rating = round(row["rating"], 1)
        result.append({
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
        })

    return result


@app.get("/api/stats")
async def stats():
    return await store.get_stats()


@app.get("/api/models")
async def list_models():
    return [{"id": m.id, "display_name": m.display_name, "categories": m.categories}
            for m in config.enabled_models()]


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
        fields = ["id", "prompt", "category", "model_a", "model_a_name", "model_b", "model_b_name",
                   "winner", "latency_a_ms", "latency_b_ms", "tokens_a", "tokens_b",
                   "cost_a", "cost_b", "created_at", "voted_at"]
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
    return FileResponse("static/index.html")


@app.get("/leaderboard")
async def leaderboard_page():
    return FileResponse("static/index.html")


app.mount("/", StaticFiles(directory="static", html=True), name="static")
