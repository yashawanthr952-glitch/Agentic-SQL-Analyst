"""Prompt templates for the plan and generate nodes.

The generate prompt has a repair slot. If that slot is ever left unfilled on a
retry, the loop degrades from "repair" to "blind retry" while still looking like
it works -- the graph still cycles, the trace still shows attempts. That is the
single easiest way to make the repair claim quietly false, which is why the slot
is built into the template rather than concatenated at the call site.
"""

from __future__ import annotations

PLAN_SYSTEM = """You are a data analyst planning how to answer a question with SQL.

You do NOT write SQL. You produce a short, concrete plan: which tables to read,
how they join, what to filter, what to aggregate, and how to order the result.

Ground every step in the schema you are given. If the question cannot be
answered from this schema, say so in `notes` and give the closest answerable
interpretation in `steps`."""

PLAN_USER = """Database schema (PostgreSQL):

{schema}

Question: {question}

Produce the analysis plan."""


GENERATE_SYSTEM = """You write PostgreSQL SELECT statements from an analysis plan.

Hard rules:
- Emit exactly ONE statement. No semicolon-separated statements.
- SELECT (or WITH ... SELECT) only. Never INSERT, UPDATE, DELETE, CREATE, DROP,
  ALTER, GRANT, COPY, SET, or SELECT ... INTO.
- Use only tables and columns that appear in the schema given to you.
- Always include a LIMIT unless the query is a single-row aggregate.
- Prefer explicit JOIN ... ON over comma joins.
- Return the SQL alone in the `sql` field, with no markdown fences."""

GENERATE_USER = """Database schema (PostgreSQL):

{schema}

Question: {question}

Analysis plan:
{plan}
{repair_block}
Write the SQL."""


# Rendered into GENERATE_USER only on a repair pass. The blank line handling
# keeps the base prompt byte-identical on the first attempt, which keeps the
# prompt cache prefix stable.
REPAIR_BLOCK = """
The previous attempt FAILED. Fix it.

Previous SQL:
{previous_sql}

{failure_kind}:
{error}

Rewrite the query so this specific failure cannot happen again. Do not repeat
the previous query.
"""


def build_plan_prompt(schema: str, question: str) -> str:
    return PLAN_USER.format(schema=schema, question=question)


def build_generate_prompt(
    schema: str,
    question: str,
    plan: str,
    repair_instruction: str | None = None,
) -> str:
    return GENERATE_USER.format(
        schema=schema,
        question=question,
        plan=plan,
        repair_block=repair_instruction or "",
    )


def build_repair_block(previous_sql: str, failure_kind: str, error: str) -> str:
    return REPAIR_BLOCK.format(
        previous_sql=previous_sql, failure_kind=failure_kind, error=error
    )
