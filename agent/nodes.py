"""Graph nodes. Each is a pure state -> partial-state function.

Nodes are built by factories so that static dependencies (the model client, the
rendered schema, the database, the validator) are closed over instead of being
serialized into state at every checkpoint.
"""

from __future__ import annotations

import datetime as dt
import decimal
import logging
import time
import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from agent import prompts
from agent.llm import LlmClient, LlmRefusal
from agent.state import AgentState
from api.database import Database
from guardrails.validator import SqlValidator

logger = logging.getLogger(__name__)

Node = Callable[[AgentState], dict]


class AgentDeps:
    """Everything the nodes need that is not per-request state."""

    def __init__(
        self,
        llm: LlmClient,
        schema_ddl: str,
        db: Database,
        validator: SqlValidator,
        max_retries: int = 3,
    ) -> None:
        self.llm = llm
        self.schema_ddl = schema_ddl
        self.db = db
        self.validator = validator
        self.max_retries = max_retries


# -- helpers ---------------------------------------------------------------


def _jsonable(value: Any) -> Any:
    """Coerce driver types the JSON encoder will not accept.

    Postgres NUMERIC comes back as Decimal and timestamps as datetime; both blow
    up FastAPI response encoding if passed through untouched.
    """
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, (dt.timedelta,)):
        return str(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (bytes, memoryview)):
        return f"<{len(bytes(value))} bytes>"
    return value


def _driver_error(exc: SQLAlchemyError) -> str:
    """The DBAPI message, not the SQLAlchemy wrapper.

    This matters for repair quality: exc.orig is `column "revenu" does not
    exist`, while str(exc) buries that under the wrapper class name and a copy
    of the full statement.
    """
    orig = getattr(exc, "orig", None)
    return str(orig).strip() if orig is not None else str(exc).strip()


# -- nodes -----------------------------------------------------------------


def make_plan_node(deps: AgentDeps) -> Node:
    def plan(state: AgentState) -> dict:
        """Decompose user_query into a plan (tables, joins, aggregations).

        Reads:  user_query
        Writes: plan, plan_tables
        """
        question = state["user_query"]
        prompt = prompts.build_plan_prompt(deps.schema_ddl, question)

        try:
            result = deps.llm.plan(prompts.PLAN_SYSTEM, prompt)
        except LlmRefusal as exc:
            return {"status": "failed", "failure_reason": str(exc)}

        logger.info("plan: %d steps over %s", len(result.steps), result.tables)
        return {"plan": result.render(), "plan_tables": result.tables}

    return plan


def make_generate_node(deps: AgentDeps) -> Node:
    def generate(state: AgentState) -> dict:
        """Emit SQL from plan + schema. The ONLY node that produces SQL.

        Reads:  plan, user_query, repair_instruction (None on the first pass)
        Writes: generated_sql, explanation; clears repair_instruction

        The repair_instruction read is what makes this a repair loop rather than
        a blind retry. If this node stopped passing it through, the graph would
        still cycle and the trace would still show three attempts -- it would
        just be regenerating from identical inputs every time.
        """
        repair_instruction = state.get("repair_instruction")
        prompt = prompts.build_generate_prompt(
            schema=deps.schema_ddl,
            question=state["user_query"],
            plan=state.get("plan") or "(no plan available)",
            repair_instruction=repair_instruction,
        )

        try:
            result = deps.llm.generate(prompts.GENERATE_SYSTEM, prompt)
        except LlmRefusal as exc:
            return {"status": "failed", "failure_reason": str(exc)}

        logger.info(
            "generate: attempt %d, repair=%s", state.get("retry_count", 0) + 1,
            bool(repair_instruction),
        )
        return {
            "generated_sql": result.sql.strip(),
            "explanation": result.explanation,
            "repair_instruction": None,  # consumed
        }

    return generate


def make_validate_node(deps: AgentDeps) -> Node:
    def validate(state: AgentState) -> dict:
        """AST guardrail. Fails closed: a parse error is a rejection.

        Reads:  generated_sql
        Writes: validation_result, safe_sql (only when ok), attempts
        """
        sql = state.get("generated_sql") or ""
        result = deps.validator.validate(sql)
        n = state.get("retry_count", 0) + 1

        attempt = {
            "n": n,
            "sql": sql,
            "stage": "validate",
            "ok": result.ok,
            "error": result.reason,
        }

        if not result.ok:
            logger.warning("validate: rejected -- %s", result.reason)
            return {
                "validation_result": result.as_dict(),
                "safe_sql": None,
                "attempts": [attempt],
            }

        # safe_sql is the rewritten text, never generated_sql. The LIMIT
        # injection lives only here.
        return {
            "validation_result": result.as_dict(),
            "safe_sql": result.rewritten_sql,
            "attempts": [attempt],
        }

    return validate


def make_execute_node(deps: AgentDeps) -> Node:
    def execute(state: AgentState) -> dict:
        """Run safe_sql against Postgres in a read-only, timed-out transaction.

        Reads:  safe_sql
        Writes: execution_result + status on success; db_error on failure;
                attempts either way
        """
        safe_sql = state.get("safe_sql")
        if not safe_sql:
            # Never fall back to generated_sql. Reaching execute without a
            # validated statement is a wiring bug, and silently running the
            # unvalidated text is exactly the failure this guards.
            raise RuntimeError("execute reached without safe_sql; check graph wiring")

        n = state.get("retry_count", 0) + 1
        started = time.perf_counter()

        try:
            with deps.db.readonly_connection() as conn:
                # exec_driver_sql, not text(): text() parses ":name" as a bind
                # parameter, so a generated literal like WHERE tag = ':pending'
                # would raise instead of running. We never bind anything here --
                # the statement is fully literal and already validated.
                cursor = conn.exec_driver_sql(safe_sql)
                columns = list(cursor.keys())
                rows = [[_jsonable(v) for v in row] for row in cursor.fetchall()]
        except SQLAlchemyError as exc:
            message = _driver_error(exc)
            logger.warning("execute: db error -- %s", message)
            return {
                "db_error": message,
                "execution_result": None,
                "attempts": [
                    {"n": n, "sql": safe_sql, "stage": "execute", "ok": False, "error": message}
                ],
            }

        elapsed_ms = (time.perf_counter() - started) * 1000
        return {
            "execution_result": {
                "columns": columns,
                "rows": rows,
                "row_count": len(rows),
                "elapsed_ms": round(elapsed_ms, 2),
            },
            "db_error": None,
            "status": "succeeded",
            "attempts": [
                {"n": n, "sql": safe_sql, "stage": "execute", "ok": True, "error": None}
            ],
        }

    return execute


# Substrings that mean the *plan* is wrong, not the SQL. Regenerating SQL from
# the same plan would produce the same bad table name forever, so these route
# back to plan instead.
_SCHEMA_ERROR_MARKERS = ("does not exist", "unknown column", "undefined table")


def make_repair_node(deps: AgentDeps) -> Node:
    def repair(state: AgentState) -> dict:
        """Turn a failure into an instruction for generate. No model call.

        Reads:  db_error OR validation_result, generated_sql, retry_count
        Writes: repair_instruction, retry_count + 1, repair_target;
                clears db_error and validation_result

        This is the only node that increments retry_count.

        Keeping repair free of SQL generation is deliberate: `generate` stays
        the single node that emits SQL, so there is exactly one prompt to
        maintain and exactly one path into `validate`. A repair node that
        emitted its own SQL would need its own edge to the guardrail, and one
        careless repair -> execute edge would bypass it.
        """
        db_error = state.get("db_error")
        validation = state.get("validation_result") or {}

        if db_error:
            failure_kind = "PostgreSQL rejected it with"
            error = db_error
            target = (
                "plan"
                if any(m in db_error.lower() for m in _SCHEMA_ERROR_MARKERS)
                else "generate"
            )
        else:
            failure_kind = "The read-only guardrail rejected it because"
            error = validation.get("reason") or "unknown validation failure"
            target = "generate"

        next_retry = state.get("retry_count", 0) + 1

        # Out of budget. The give-up decision is made here, not in the router,
        # because a router cannot write state -- and without an explicit
        # "failed" status the API cannot tell a successful run from a run that
        # exhausted its retries, since both simply arrive at END.
        if next_retry > deps.max_retries:
            logger.warning("repair: giving up after %d attempts", deps.max_retries)
            return {
                "retry_count": next_retry,
                "status": "failed",
                "failure_reason": f"gave up after {deps.max_retries} attempts. Last error: {error}",
                "db_error": None,
                "validation_result": None,
            }

        instruction = prompts.build_repair_block(
            previous_sql=state.get("generated_sql") or "(none)",
            failure_kind=failure_kind,
            error=error,
        )

        logger.info("repair: retry %d -> %s (%s)", next_retry, target, error[:80])
        return {
            "repair_instruction": instruction,
            "retry_count": next_retry,
            "repair_target": target,
            # Cleared so a stale error cannot be re-read on the next pass.
            "db_error": None,
            "validation_result": None,
        }

    return repair
