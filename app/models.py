from __future__ import annotations

from pydantic import BaseModel


class BattleRequest(BaseModel):
    prompt: str
    category: str = "general"
    model_a: str | None = None
    model_b: str | None = None
    # "low" | "medium" | "high", or null/"off" to leave thinking at the provider default.
    reasoning_effort: str | None = None


class VoteRequest(BaseModel):
    winner: str  # "a", "b", or "tie"


class AudienceVoteRequest(BaseModel):
    voter_id: str  # client-generated, stored in the phone's localStorage
    choice: str  # "a", "b", or "tie"
