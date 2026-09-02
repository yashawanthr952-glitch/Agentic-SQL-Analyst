"""LangGraph state for the SQL analyst loop.

Design notes that matter (the README expands on each):

* ``db_error`` is a key of its own rather than a field inside
  ``execution_result``. Two reasons. First, provability: the repair node reads
  ``state["db_error"]``, and that key is written in exactly one place, so "the
  DB error is fed back into repair" is a one-grep claim rather than a story.
  Second, a validation failure and an execution failure need different repair
  instructions, so the repair node branches on *which* key is populated instead
  of sniffing strings.

* ``generated_sql`` and ``safe_sql`` are separate. ``generated_sql`` is the
  verbatim model output, kept for an honest trace. ``safe_sql`` is what the
  guardrail produced and the only thing ``execute`` will run. If execute ever
  fell back to ``generated_sql`` the LIMIT rewrite would be silently discarded.

* ``retry_count`` lives in state, not in a counter. Nodes must be pure
  state -> partial-state so the graph can be checkpointed, interrupted and
  resumed; a module-level counter would also leak across concurrent requests
  under FastAPI.

* ``attempts`` uses an append reducer. Everything else is last-write-wins,
  which is right for a current-value field and wrong for a trace -- the
  frontend needs plan / attempt 1 / error / attempt 2 / final, and an
  overwriting reducer would only ever show the last one.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, Literal, TypedDict


class Attempt(TypedDict):
    """One pass through generate -> validate -> execute. Append-only trace."""

    n: int
    sql: str
    stage: Literal["validate", "execute"]
    ok: bool
    error: str | None


class ExecutionResult(TypedDict):
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    elapsed_ms: float


class AgentState(TypedDict, total=False):
    # --- input ---
    user_query: str

    # --- plan stage ---
    plan: str | None
    plan_tables: list[str]

    # --- generate stage ---
    generated_sql: str | None  # verbatim model output; never executed
    explanation: str | None
    repair_instruction: str | None  # written by repair, consumed by generate

    # --- validate stage ---
    validation_result: dict[str, Any] | None
    safe_sql: str | None  # post-guardrail; the ONLY thing execute runs

    # --- execute stage ---
    execution_result: ExecutionResult | None
    db_error: str | None  # driver error text, verbatim, for repair

    # --- loop control ---
    retry_count: int  # incremented in repair, and nowhere else
    repair_target: Literal["plan", "generate"]
    status: Literal["running", "succeeded", "failed"]
    failure_reason: str | None

    # --- trace (append reducer; drives the frontend step view) ---
    attempts: Annotated[list[Attempt], operator.add]


def initial_state(user_query: str) -> AgentState:
    """Every key the nodes read must be seeded, so no node has to guess whether
    a missing key means "not set yet" or "cleared by a previous pass"."""
    return AgentState(
        user_query=user_query,
        plan=None,
        plan_tables=[],
        generated_sql=None,
        explanation=None,
        repair_instruction=None,
        validation_result=None,
        safe_sql=None,
        execution_result=None,
        db_error=None,
        retry_count=0,
        repair_target="generate",
        status="running",
        failure_reason=None,
        attempts=[],
    )
