from __future__ import annotations

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
"""

# Column-level additions applied idempotently after schema creation. SQLite
# doesn't support ADD COLUMN IF NOT EXISTS, so we swallow the "duplicate
# column" OperationalError from re-runs.
_ADDITIVE_COLUMNS = [
    ("vote_log", "method", "TEXT NOT NULL DEFAULT 'human'"),
    ("vote_log", "judge_reasoning", "TEXT"),
    ("vote_log", "judge_model_id", "TEXT"),
    ("vote_log", "judge_cost", "REAL"),
]


def _gen_id(length: int = 16) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


class Store:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self.db: aiosqlite.Connection | None = None

    async def connect(self):
        try:
            self.db = await aiosqlite.connect(self.db_path)
            self.db.row_factory = aiosqlite.Row
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
            await self.db.commit()
            log.info("database connected: %s", self.db_path)
        except Exception as e:
            raise StoreError(f"failed to connect to database at {self.db_path}: {e}") from e

    async def close(self):
        if self.db:
            await self.db.close()

    async def create_battle(self, prompt: str, category: str, model_a: str, model_b: str) -> str:
        battle_id = _gen_id()
        await self.db.execute(
            "INSERT INTO battles (id, prompt, category, model_a, model_b) VALUES (?, ?, ?, ?, ?)",
            (battle_id, prompt, category, model_a, model_b),
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
        await self.db.execute(
            "UPDATE battles SET response_a = ?, latency_a_ms = ?, tokens_a = ?, cost_a = ? WHERE id = ?",
            (response, latency_ms, tokens, cost, battle_id),
        )
        await self.db.commit()

    async def update_response_b(self, battle_id: str, response: str, latency_ms: int, tokens: int, cost: float):
        await self.db.execute(
            "UPDATE battles SET response_b = ?, latency_b_ms = ?, tokens_b = ?, cost_b = ? WHERE id = ?",
            (response, latency_ms, tokens, cost, battle_id),
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
    ) -> dict:
        battle = await self.get_battle(battle_id)
        if not battle:
            raise ValueError("battle not found")

        # Atomically claim the vote up front. This conditional UPDATE only
        # succeeds for the first caller (winner IS NULL); concurrent callers
        # interleaving at the await boundaries below will see rowcount == 0 and
        # bail, so the Elo update + vote_log insert run exactly once per battle.
        cursor = await self.db.execute(
            "UPDATE battles SET winner = ?, voted_at = datetime('now') WHERE id = ? AND winner IS NULL",
            (winner, battle_id),
        )
        if cursor.rowcount == 0:
            raise ValueError("already voted")

        model_a = battle["model_a"]
        model_b = battle["model_b"]

        # Get or create ratings for both models (overall + category)
        categories = ["overall", battle["category"]]
        results = {}

        for cat in categories:
            rating_a = await self._get_or_create_rating(model_a, cat)
            rating_b = await self._get_or_create_rating(model_b, cat)

            new_a, new_b = _update_elo(rating_a, rating_b, winner)

            # Update ratings
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

        # Log vote
        await self.db.execute(
            "INSERT INTO vote_log (battle_id, model_a, model_b, winner,"
            " rating_a_before, rating_b_before, rating_a_after, rating_b_after,"
            " method, judge_reasoning, judge_model_id, judge_cost)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
            ),
        )

        # Single commit: the claim above plus the Elo updates and vote_log
        # insert all land atomically.
        await self.db.commit()

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
            "method, judge_reasoning, judge_model_id, judge_cost "
            "FROM vote_log WHERE battle_id = ? ORDER BY id DESC LIMIT 1",
            (battle_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def get_all_voted_battles(self) -> list[dict]:
        cursor = await self.db.execute(
            "SELECT id, prompt, category, model_a, model_b, winner, "
            "latency_a_ms, latency_b_ms, tokens_a, tokens_b, cost_a, cost_b, "
            "created_at, voted_at FROM battles WHERE winner IS NOT NULL ORDER BY created_at DESC"
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    # --- Suite runs ---

    async def create_suite_run(self, suite_name: str, battles_total: int) -> str:
        run_id = _gen_id()
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
