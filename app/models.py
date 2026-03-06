from __future__ import annotations

from pydantic import BaseModel


class BattleRequest(BaseModel):
    prompt: str
    category: str = "general"
    model_a: str | None = None
    model_b: str | None = None


class BattleResponse(BaseModel):
    battle_id: str
    model_a: str  # just "Model A" (blind)
    model_b: str  # just "Model B" (blind)


class VoteRequest(BaseModel):
    winner: str  # "a", "b", or "tie"


class VoteResponse(BaseModel):
    model_a_id: str
    model_a_name: str
    model_b_id: str
    model_b_name: str
    latency_a_ms: int
    latency_b_ms: int
    tokens_a: int
    tokens_b: int
    cost_a: float
    cost_b: float
    rating_a_before: float
    rating_b_before: float
    rating_a_after: float
    rating_b_after: float


class LeaderboardEntry(BaseModel):
    rank: int
    model_id: str
    display_name: str
    rating: float
    wins: int
    losses: int
    ties: int
    win_rate: float
    provider: str


class StatsResponse(BaseModel):
    total_battles: int
    total_voted: int
    battles_today: int
