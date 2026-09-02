"""Request/response models for the HTTP surface.

The response deliberately exposes both `sql` (verbatim model output) and
`executed_sql` (post-guardrail). The frontend shows both so that a LIMIT the
guardrail injected is visible as something the guardrail did, rather than
looking like the model wrote it.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class QueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


class AttemptOut(BaseModel):
    n: int
    sql: str
    stage: Literal["validate", "execute"]
    ok: bool
    error: str | None = None


class ResultOut(BaseModel):
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    elapsed_ms: float


class QueryResponse(BaseModel):
    status: Literal["succeeded", "failed"]
    question: str
    plan: str | None = None
    sql: str | None = None
    executed_sql: str | None = None
    explanation: str | None = None
    result: ResultOut | None = None
    attempts: list[AttemptOut] = []
    retry_count: int = 0
    failure_reason: str | None = None


class HealthResponse(BaseModel):
    ok: bool
    database: bool
    schema_loaded: bool
