from __future__ import annotations

import asyncio
import logging
import secrets
import string

import aiosqlite

from .errors import StoreError

log = logging.getLogger("arena.store")

DB_PATH = "data/arena.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS battles (
    id TEXT PRIMARY KEY,
    prompt TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'general',
    model_a TEXT NOT NULL,
    model_b TEXT NOT NULL,
    response_a TEXT DEFAULT '',
    response_b TEXT DEFAULT '',
    winner TEXT,
    latency_a_ms INTEGER DEFAULT 0,
    latency_b_ms INTEGER DEFAULT 0,
    tokens_a INTEGER DEFAULT 0,
    tokens_b INTEGER DEFAULT 0,
    cost_a REAL DEFAULT 0.0,
    cost_b REAL DEFAULT 0.0,
    created_at DATETIME NOT NULL DEFAULT (datetime('now')),
    voted_at DATETIME
);

CREATE TABLE IF NOT EXISTS ratings (
    model_id TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'overall',
    rating REAL NOT NULL DEFAULT 1500.0,
    wins INTEGER DEFAULT 0,
    losses INTEGER DEFAULT 0,
    ties INTEGER DEFAULT 0,
    updated_at DATETIME NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (model_id, category)
);

CREATE TABLE IF NOT EXISTS suite_runs (
    id TEXT PRIMARY KEY,
    suite_name TEXT NOT NULL,
    started_at DATETIME NOT NULL DEFAULT (datetime('now')),
    finished_at DATETIME,
    status TEXT NOT NULL DEFAULT 'running',
    battles_total INTEGER NOT NULL,
    battles_completed INTEGER NOT NULL DEFAULT 0,
    battles_errored INTEGER NOT NULL DEFAULT 0,
    total_cost REAL NOT NULL DEFAULT 0.0
);

CREATE TABLE IF NOT EXISTS suite_battles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    prompt_id TEXT NOT NULL,
    battle_id TEXT,
    winner TEXT,
    error TEXT,
    FOREIGN KEY (run_id) REFERENCES suite_runs(id)
);

CREATE INDEX IF NOT EXISTS idx_suite_battles_run ON suite_battles(run_id);

CREATE TABLE IF NOT EXISTS vote_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    battle_id TEXT NOT NULL,
    model_a TEXT NOT NULL,
    model_b TEXT NOT NULL,
    winner TEXT NOT NULL,
    rating_a_before REAL,
    rating_b_before REAL,
    rating_a_after REAL,
    rating_b_after REAL,
    created_at DATETIME NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS polls (
    code TEXT PRIMARY KEY,
    battle_id TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'open',
    created_at DATETIME NOT NULL DEFAULT (datetime('now')),
    closed_at DATETIME
);

CREATE TABLE IF NOT EXISTS poll_votes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    poll_code TEXT NOT NULL,
    voter_id TEXT NOT NULL,
    choice TEXT NOT NULL,
    created_at DATETIME NOT NULL DEFAULT (datetime('now')),
    updated_at DATETIME NOT NULL DEFAULT (datetime('now')),
    UNIQUE (poll_code, voter_id),
    FOREIGN KEY (poll_code) REFERENCES polls(code)
);
"""

# Column-level additions applied idempotently after schema creation. SQLite
# doesn't support ADD COLUMN IF NOT EXISTS, so we swallow the "duplicate
# column" OperationalError from re-runs.
_ADDITIVE_COLUMNS = [
    ("vote_log", "method", "TEXT NOT NULL DEFAULT 'human'"),
    ("vote_log", "judge_reasoning", "TEXT"),
    ("vote_log", "judge_model_id", "TEXT"),
    ("vote_log", "judge_cost", "REAL"),
    ("battles", "execution_state", "TEXT"),
    ("battles", "reasoning_effort", "TEXT"),
    ("vote_log", "audience_tally", "TEXT"),
]

# Poll lifecycle. A poll is open for at most POLL_TTL_SECONDS after creation;
# past that it reads as 'expired' and refuses votes, so a leaked join code
# from last week's class cannot be replayed into this week's leaderboard.
POLL_STATUS_OPEN = "open"
POLL_STATUS_CLOSED = "closed"
POLL_STATUS_EXPIRED = "expired"
POLL_TTL_SECONDS = 6 * 3600
POLL_MAX_VOTERS = 1000
# Unambiguous alphabet for join codes typed from a projector: no 0/O, 1/I/L.
_POLL_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"

# execution_state values on the battles row:
#   NULL / 'pending' → never streamed, safe to claim
#   'running'        → a stream_battle call holds the claim
#   'complete'       → both responses persisted, stream may replay them
#   'error'          → a prior stream failed; do not restream (operator only)
EXEC_STATE_RUNNING = "running"
EXEC_STATE_COMPLETE = "complete"
EXEC_STATE_ERROR = "error"


def _gen_id(length: int = 16) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _gen_poll_code(length: int = 6) -> str:
    return "".join(secrets.choice(_POLL_CODE_ALPHABET) for _ in range(length))


class Store:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self.db: aiosqlite.Connection | None = None
        # All writes and read-modify-write transactions serialize on this lock.
        # The Store shares a single aiosqlite connection across coroutines, so
        # any commit() on it flushes every pending statement — including work
        # begun by another coroutine. Serializing keeps transaction boundaries
        # honest and matches SQLite's single-writer semantics.
        self._write_lock: asyncio.Lock | None = None

    async def connect(self):
        try:
            self.db = await aiosqlite.connect(self.db_path)
            self.db.row_factory = aiosqlite.Row
            self._write_lock = asyncio.Lock()
            await self.db.execute("PRAGMA journal_mode=WAL")
            await self.db.executescript(SCHEMA)
            for table, column, spec in _ADDITIVE_COLUMNS:
                try:
                    await self.db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {spec}")
                except aiosqlite.OperationalError as e:
                    # Column already exists — the ALTER for it landed on a
                    # prior boot. SQLite has no ADD COLUMN IF NOT EXISTS.
                    if "duplicate column" not in str(e).lower():
                        raise
            # Backfill execution_state for battles that already have responses
            # persisted from before this column existed, so a first stream
            # request post-upgrade doesn't reclaim + rerun a completed battle.
            await self.db.execute(
                "UPDATE battles SET execution_state = ? "
                "WHERE execution_state IS NULL "
                "AND (COALESCE(response_a, '') != '' OR COALESCE(response_b, '') != '')",
                (EXEC_STATE_COMPLETE,),
            )
            await self.db.commit()
            log.info("database connected: %s", self.db_path)
        except Exception as e:
            raise StoreError(f"failed to connect to database at {self.db_path}: {e}") from e

    async def close(self):
        if self.db:
            await self.db.close()

    async def create_battle(
        self,
        prompt: str,
        category: str,
        model_a: str,
        model_b: str,
        reasoning_effort: str | None = None,
    ) -> str:
        battle_id = _gen_id()
        async with self._write_lock:
            await self.db.execute(
                "INSERT INTO battles (id, prompt, category, model_a, model_b, reasoning_effort)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (battle_id, prompt, category, model_a, model_b, reasoning_effort),
            )
            await self.db.commit()
        return battle_id

    async def get_battle(self, battle_id: str) -> dict | None:
        cursor = await self.db.execute("SELECT * FROM battles WHERE id = ?", (battle_id,))
        row = await cursor.fetchone()
        if not row:
            return None
        return dict(row)

    async def update_response_a(self, battle_id: str, response: str, latency_ms: int, tokens: int, cost: float):
        async with self._write_lock:
            await self.db.execute(
                "UPDATE battles SET response_a = ?, latency_a_ms = ?, tokens_a = ?, cost_a = ? WHERE id = ?",
                (response, latency_ms, tokens, cost, battle_id),
            )
            await self.db.commit()

    async def update_response_b(self, battle_id: str, response: str, latency_ms: int, tokens: int, cost: float):
        async with self._write_lock:
            await self.db.execute(
                "UPDATE battles SET response_b = ?, latency_b_ms = ?, tokens_b = ?, cost_b = ? WHERE id = ?",
                (response, latency_ms, tokens, cost, battle_id),
            )
            await self.db.commit()

    async def claim_battle_execution(self, battle_id: str) -> tuple[bool, str | None]:
        """Atomically transition a battle from pending → running.

        Returns ``(claimed, current_state)``. ``claimed`` is True only when
        this call flipped the row from pending to running; the caller then owns
        execution. When False, ``current_state`` reflects why the claim was
        refused ('running', 'complete', 'error', or 'voted').
        """
        async with self._write_lock:
            cursor = await self.db.execute(
                "UPDATE battles SET execution_state = ? "
                "WHERE id = ? "
                "AND (execution_state IS NULL OR execution_state = 'pending') "
                "AND winner IS NULL",
                (EXEC_STATE_RUNNING, battle_id),
            )
            if cursor.rowcount == 1:
                await self.db.commit()
                return True, EXEC_STATE_RUNNING
            await self.db.commit()
        # Not claimable — figure out why so the caller can pick a response.
        cursor = await self.db.execute(
            "SELECT execution_state, winner FROM battles WHERE id = ?",
            (battle_id,),
        )
        row = await cursor.fetchone()
        if not row:
            return False, None
        if row["winner"] is not None:
            return False, "voted"
        return False, row["execution_state"]

    async def mark_battle_execution(self, battle_id: str, state: str) -> None:
        """Record the outcome of a claimed execution (complete or error)."""
        if state not in (EXEC_STATE_COMPLETE, EXEC_STATE_ERROR):
            raise ValueError(f"invalid execution state: {state}")
        async with self._write_lock:
            await self.db.execute(
                "UPDATE battles SET execution_state = ? WHERE id = ?",
                (state, battle_id),
            )
            await self.db.commit()

    async def record_vote(
        self,
        battle_id: str,
        winner: str,
        method: str = "human",
        judge_reasoning: str | None = None,
        judge_model_id: str | None = None,
        judge_cost: float | None = None,
        audience_tally: str | None = None,
    ) -> dict:
        # Existence check outside the lock is a fast-path; the real check
        # happens inside the transaction below via the conditional UPDATE.
        battle = await self.get_battle(battle_id)
        if not battle:
            raise ValueError("battle not found")

        model_a = battle["model_a"]
        model_b = battle["model_b"]
        # Overall + the battle's own category. dedup so that when a battle is
        # created with category='overall' we only update once — otherwise the
        # loop below would double-apply the Elo delta and the win/loss counter.
        categories = list(dict.fromkeys(["overall", battle["category"]]))
        results: dict = {}

        async with self._write_lock:
            # Explicit transaction: the claim, Elo updates, and vote_log insert
            # must land together, and we need a rollback path if anything below
            # fails so partial state doesn't leak out via a later commit.
            await self.db.execute("BEGIN IMMEDIATE")
            try:
                # Atomically claim the vote up front. Conditional UPDATE only
                # succeeds for the first caller (winner IS NULL); concurrent
                # callers see rowcount == 0 and bail with 'already voted'.
                cursor = await self.db.execute(
                    "UPDATE battles SET winner = ?, voted_at = datetime('now') WHERE id = ? AND winner IS NULL",
                    (winner, battle_id),
                )
                if cursor.rowcount == 0:
                    await self.db.rollback()
                    raise ValueError("already voted")

                for cat in categories:
                    rating_a = await self._get_or_create_rating(model_a, cat)
                    rating_b = await self._get_or_create_rating(model_b, cat)

                    new_a, new_b = _update_elo(rating_a, rating_b, winner)

                    _update_sql = (
                        "UPDATE ratings SET rating = ?, {stat} = {stat} + 1,"
                        " updated_at = datetime('now') WHERE model_id = ? AND category = ?"
                    )
                    if winner == "a":
                        await self.db.execute(_update_sql.format(stat="wins"), (new_a, model_a, cat))
                        await self.db.execute(_update_sql.format(stat="losses"), (new_b, model_b, cat))
                    elif winner == "b":
                        await self.db.execute(_update_sql.format(stat="losses"), (new_a, model_a, cat))
                        await self.db.execute(_update_sql.format(stat="wins"), (new_b, model_b, cat))
                    else:  # tie
                        await self.db.execute(_update_sql.format(stat="ties"), (new_a, model_a, cat))
                        await self.db.execute(_update_sql.format(stat="ties"), (new_b, model_b, cat))

                    if cat == "overall":
                        results = {
                            "rating_a_before": rating_a,
                            "rating_b_before": rating_b,
                            "rating_a_after": new_a,
                            "rating_b_after": new_b,
                        }

                await self.db.execute(
                    "INSERT INTO vote_log (battle_id, model_a, model_b, winner,"
                    " rating_a_before, rating_b_before, rating_a_after, rating_b_after,"
                    " method, judge_reasoning, judge_model_id, judge_cost, audience_tally)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        battle_id,
                        model_a,
                        model_b,
                        winner,
                        results["rating_a_before"],
                        results["rating_b_before"],
                        results["rating_a_after"],
                        results["rating_b_after"],
                        method,
                        judge_reasoning,
                        judge_model_id,
                        judge_cost,
                        audience_tally,
                    ),
                )
                await self.db.commit()
            except Exception:
                # aiosqlite treats rollback on a non-active transaction as a
                # no-op error; swallow it so we can re-raise the original.
                try:
                    await self.db.rollback()
                except Exception:  # noqa: BLE001
                    pass
                raise

        return results

    async def _get_or_create_rating(self, model_id: str, category: str) -> float:
        cursor = await self.db.execute(
            "SELECT rating FROM ratings WHERE model_id = ? AND category = ?",
            (model_id, category),
        )
        row = await cursor.fetchone()
        if row:
            return row["rating"]
        await self.db.execute(
            "INSERT INTO ratings (model_id, category) VALUES (?, ?)",
            (model_id, category),
        )
        return 1500.0

    async def get_leaderboard(self, category: str = "overall") -> list[dict]:
        cursor = await self.db.execute(
            "SELECT model_id, rating, wins, losses, ties FROM ratings WHERE category = ? ORDER BY rating DESC",
            (category,),
        )
        rows = await cursor.fetchall()
        result = [dict(r) for r in rows]

        # Attach avg latency per model from voted battles
        for row in result:
            mid = row["model_id"]
            lat_cursor = await self.db.execute(
                "SELECT AVG(latency) as avg_latency FROM ("
                "  SELECT latency_a_ms as latency FROM battles WHERE model_a = ? AND winner IS NOT NULL"
                "  UNION ALL"
                "  SELECT latency_b_ms as latency FROM battles WHERE model_b = ? AND winner IS NOT NULL"
                ") t",
                (mid, mid),
            )
            lat_row = await lat_cursor.fetchone()
            row["avg_latency_ms"] = round(lat_row["avg_latency"]) if lat_row["avg_latency"] else 0

        return result

    async def get_vote_log(self, battle_id: str) -> dict | None:
        """Return the ELO delta + method row for a battle's vote, or None if unvoted."""
        cursor = await self.db.execute(
            "SELECT rating_a_before, rating_b_before, rating_a_after, rating_b_after, "
            "method, judge_reasoning, judge_model_id, judge_cost, audience_tally "
            "FROM vote_log WHERE battle_id = ? ORDER BY id DESC LIMIT 1",
            (battle_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def get_all_voted_battles(self) -> list[dict]:
        cursor = await self.db.execute(
            "SELECT id, prompt, category, model_a, model_b, winner, reasoning_effort, "
            "latency_a_ms, latency_b_ms, tokens_a, tokens_b, cost_a, cost_b, "
            "created_at, voted_at FROM battles WHERE winner IS NOT NULL ORDER BY created_at DESC"
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    # --- Audience polls ---

    async def create_poll(self, battle_id: str) -> dict:
        """Open an audience poll for a battle, or return the existing one.

        One poll per battle. Codes are retried on the (rare) collision.
        """
        existing = await self.get_poll_for_battle(battle_id)
        if existing:
            return existing
        async with self._write_lock:
            for _ in range(10):
                code = _gen_poll_code()
                try:
                    await self.db.execute(
                        "INSERT INTO polls (code, battle_id) VALUES (?, ?)",
                        (code, battle_id),
                    )
                except aiosqlite.IntegrityError as e:
                    msg = str(e).lower()
                    if "polls.battle_id" in msg:
                        # Lost a race with another opener; return theirs.
                        await self.db.rollback()
                        break
                    continue  # code collision, draw again
                await self.db.commit()
                break
            else:
                raise StoreError("could not allocate a poll code")
        poll = await self.get_poll_for_battle(battle_id)
        if not poll:
            raise StoreError("poll vanished after creation")
        return poll

    def _poll_row(self, row) -> dict:
        poll = dict(row)
        if poll["status"] == POLL_STATUS_OPEN and (poll.get("age_s") or 0) > POLL_TTL_SECONDS:
            poll["status"] = POLL_STATUS_EXPIRED
        poll.pop("age_s", None)
        return poll

    async def get_poll(self, code: str) -> dict | None:
        cursor = await self.db.execute(
            "SELECT code, battle_id, status, created_at, closed_at,"
            " (strftime('%s','now') - strftime('%s', created_at)) AS age_s FROM polls WHERE code = ?",
            (code,),
        )
        row = await cursor.fetchone()
        return self._poll_row(row) if row else None

    async def get_poll_for_battle(self, battle_id: str) -> dict | None:
        cursor = await self.db.execute(
            "SELECT code, battle_id, status, created_at, closed_at,"
            " (strftime('%s','now') - strftime('%s', created_at)) AS age_s FROM polls WHERE battle_id = ?",
            (battle_id,),
        )
        row = await cursor.fetchone()
        return self._poll_row(row) if row else None

    async def cast_poll_vote(self, code: str, voter_id: str, choice: str) -> None:
        """Record or change one voter's choice on an open poll.

        Raises ValueError when the poll is missing, not open, or full.
        """
        if choice not in ("a", "b", "tie"):
            raise ValueError("choice must be 'a', 'b', or 'tie'")
        async with self._write_lock:
            poll = await self.get_poll(code)
            if not poll:
                raise ValueError("poll not found")
            if poll["status"] != POLL_STATUS_OPEN:
                raise ValueError(f"poll is {poll['status']}")
            cursor = await self.db.execute(
                "SELECT COUNT(*) AS c FROM poll_votes WHERE poll_code = ? AND voter_id != ?",
                (code, voter_id),
            )
            row = await cursor.fetchone()
            if row["c"] >= POLL_MAX_VOTERS:
                raise ValueError("poll is full")
            await self.db.execute(
                "INSERT INTO poll_votes (poll_code, voter_id, choice) VALUES (?, ?, ?)"
                " ON CONFLICT(poll_code, voter_id) DO UPDATE SET choice = excluded.choice,"
                " updated_at = datetime('now')",
                (code, voter_id, choice),
            )
            await self.db.commit()

    async def get_poll_tally(self, code: str) -> dict:
        cursor = await self.db.execute(
            "SELECT choice, COUNT(*) AS c FROM poll_votes WHERE poll_code = ? GROUP BY choice",
            (code,),
        )
        rows = await cursor.fetchall()
        tally = {"a": 0, "b": 0, "tie": 0}
        for r in rows:
            if r["choice"] in tally:
                tally[r["choice"]] = r["c"]
        tally["total"] = tally["a"] + tally["b"] + tally["tie"]
        return tally

    async def get_poll_voter_choice(self, code: str, voter_id: str) -> str | None:
        cursor = await self.db.execute(
            "SELECT choice FROM poll_votes WHERE poll_code = ? AND voter_id = ?",
            (code, voter_id),
        )
        row = await cursor.fetchone()
        return row["choice"] if row else None

    async def close_poll(self, code: str) -> bool:
        """Flip an open poll to closed. Returns False if it was not open."""
        async with self._write_lock:
            cursor = await self.db.execute(
                "UPDATE polls SET status = ?, closed_at = datetime('now') WHERE code = ? AND status = ?",
                (POLL_STATUS_CLOSED, code, POLL_STATUS_OPEN),
            )
            await self.db.commit()
            return cursor.rowcount == 1

    # --- Cost breakdown ---

    async def get_cost_breakdown(self, days: int) -> list[dict]:
        """Sum per-model cost + token usage over the last N days.

        Rows are (model_id, total_cost, total_output_tokens, battles). Costs
        come from the ``cost_a``/``cost_b`` columns, which use real API usage
        numbers when the provider returns them (see arena.call_model).
        Judge cost is folded in via ``vote_log.judge_cost`` and attributed to
        the judge model.
        """
        window = f"-{int(days)} days"
        cursor = await self.db.execute(
            "SELECT model_id, SUM(cost) AS total_cost, "
            "SUM(tokens) AS total_output_tokens, COUNT(*) AS battles FROM ("
            "  SELECT model_a AS model_id, cost_a AS cost, tokens_a AS tokens "
            "    FROM battles WHERE created_at >= datetime('now', ?) "
            "  UNION ALL "
            "  SELECT model_b AS model_id, cost_b AS cost, tokens_b AS tokens "
            "    FROM battles WHERE created_at >= datetime('now', ?) "
            ") GROUP BY model_id",
            (window, window),
        )
        rows = await cursor.fetchall()
        result = [dict(r) for r in rows]

        # Judge cost — attribute to the judge model.
        judge_cursor = await self.db.execute(
            "SELECT judge_model_id AS model_id, SUM(judge_cost) AS judge_cost, "
            "COUNT(*) AS judgments FROM vote_log "
            "WHERE method = 'judge' AND created_at >= datetime('now', ?) "
            "AND judge_model_id IS NOT NULL GROUP BY judge_model_id",
            (window,),
        )
        judge_rows = {r["model_id"]: dict(r) for r in await judge_cursor.fetchall()}

        # Merge judge cost into the per-model rows (adds a row if the judge
        # never itself competed as an A/B model).
        by_model = {r["model_id"]: r for r in result}
        for mid, jrow in judge_rows.items():
            if mid in by_model:
                by_model[mid]["total_cost"] = (by_model[mid]["total_cost"] or 0) + (jrow["judge_cost"] or 0)
                by_model[mid]["judgments"] = jrow["judgments"]
            else:
                by_model[mid] = {
                    "model_id": mid,
                    "total_cost": jrow["judge_cost"] or 0,
                    "total_output_tokens": 0,
                    "battles": 0,
                    "judgments": jrow["judgments"],
                }

        for r in by_model.values():
            r["total_cost"] = round(r["total_cost"] or 0, 6)
            r["total_output_tokens"] = int(r["total_output_tokens"] or 0)
            r.setdefault("judgments", 0)

        return list(by_model.values())

    # --- Suite runs ---

    async def create_suite_run(self, suite_name: str, battles_total: int) -> str:
        run_id = _gen_id()
        async with self._write_lock:
            await self.db.execute(
                "INSERT INTO suite_runs (id, suite_name, battles_total) VALUES (?, ?, ?)",
                (run_id, suite_name, battles_total),
            )
            await self.db.commit()
        return run_id

    async def record_suite_battle(
        self,
        run_id: str,
        prompt_id: str,
        battle_id: str | None,
        winner: str | None,
        error: str | None,
    ) -> None:
        async with self._write_lock:
            await self.db.execute(
                "INSERT INTO suite_battles (run_id, prompt_id, battle_id, winner, error) VALUES (?, ?, ?, ?, ?)",
                (run_id, prompt_id, battle_id, winner, error),
            )
            if error:
                await self.db.execute(
                    "UPDATE suite_runs SET battles_errored = battles_errored + 1 WHERE id = ?",
                    (run_id,),
                )
            else:
                await self.db.execute(
                    "UPDATE suite_runs SET battles_completed = battles_completed + 1 WHERE id = ?",
                    (run_id,),
                )
            await self.db.commit()

    async def finish_suite_run(self, run_id: str, status: str, total_cost: float) -> None:
        async with self._write_lock:
            await self.db.execute(
                "UPDATE suite_runs SET finished_at = datetime('now'), status = ?, total_cost = ? WHERE id = ?",
                (status, total_cost, run_id),
            )
            await self.db.commit()

    async def get_suite_run(self, run_id: str) -> dict | None:
        cursor = await self.db.execute("SELECT * FROM suite_runs WHERE id = ?", (run_id,))
        row = await cursor.fetchone()
        if not row:
            return None
        run = dict(row)

        # Join to per-battle results, aggregate wins-per-model.
        cursor = await self.db.execute(
            "SELECT sb.prompt_id, sb.battle_id, sb.winner, sb.error, "
            "b.model_a, b.model_b, b.cost_a, b.cost_b "
            "FROM suite_battles sb LEFT JOIN battles b ON sb.battle_id = b.id "
            "WHERE sb.run_id = ? ORDER BY sb.id",
            (run_id,),
        )
        rows = await cursor.fetchall()
        battles = [dict(r) for r in rows]

        tally: dict[str, dict] = {}
        for b in battles:
            for side in ("a", "b"):
                model = b[f"model_{side}"]
                if not model:
                    continue
                if model not in tally:
                    tally[model] = {"wins": 0, "losses": 0, "ties": 0, "battles": 0}
                tally[model]["battles"] += 1
            if b["winner"] == "a":
                tally[b["model_a"]]["wins"] += 1
                tally[b["model_b"]]["losses"] += 1
            elif b["winner"] == "b":
                tally[b["model_b"]]["wins"] += 1
                tally[b["model_a"]]["losses"] += 1
            elif b["winner"] == "tie":
                tally[b["model_a"]]["ties"] += 1
                tally[b["model_b"]]["ties"] += 1

        run["battles"] = battles
        run["tally"] = tally
        return run

    async def list_suite_runs(self, suite_name: str, limit: int = 20) -> list[dict]:
        cursor = await self.db.execute(
            "SELECT id, suite_name, started_at, finished_at, status, "
            "battles_total, battles_completed, battles_errored, total_cost "
            "FROM suite_runs WHERE suite_name = ? ORDER BY started_at DESC LIMIT ?",
            (suite_name, limit),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def get_stats(self) -> dict:
        total = await self.db.execute("SELECT COUNT(*) as c FROM battles")
        total_row = await total.fetchone()

        voted = await self.db.execute("SELECT COUNT(*) as c FROM battles WHERE winner IS NOT NULL")
        voted_row = await voted.fetchone()

        today = await self.db.execute("SELECT COUNT(*) as c FROM battles WHERE created_at >= date('now', 'localtime')")
        today_row = await today.fetchone()

        return {
            "total_battles": total_row["c"],
            "total_voted": voted_row["c"],
            "battles_today": today_row["c"],
        }


def _update_elo(rating_a: float, rating_b: float, winner: str) -> tuple[float, float]:
    k = 32
    ea = 1 / (1 + 10 ** ((rating_b - rating_a) / 400))
    eb = 1 - ea
    if winner == "a":
        sa, sb = 1.0, 0.0
    elif winner == "b":
        sa, sb = 0.0, 1.0
    else:
        sa, sb = 0.5, 0.5
    return rating_a + k * (sa - ea), rating_b + k * (sb - eb)
