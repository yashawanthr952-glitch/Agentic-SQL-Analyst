"""End-to-end graph tests with a fake model and a fake database.

These exist to keep three claims honest. Each one is easy to break in a way that
leaves the graph looking fine -- it still cycles, the trace still shows
attempts, the API still returns 200:

1. The DB error text actually reaches the prompt on the retry.
2. The guardrail rejection reason actually reaches the prompt on the retry.
3. The loop is bounded and reports failure explicitly.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlalchemy.exc import ProgrammingError

from agent.graph import build_graph
from agent.llm import GeneratedSql, QueryPlan
from agent.nodes import AgentDeps
from agent.state import initial_state
from guardrails.validator import SqlValidator


class FakeLlm:
    """Returns scripted SQL and records every prompt it was given."""

    def __init__(self, sqls: list[str]) -> None:
        self._sqls = list(sqls)
        self.generate_prompts: list[str] = []
        self.plan_prompts: list[str] = []

    def plan(self, system: str, prompt: str) -> QueryPlan:
        self.plan_prompts.append(prompt)
        return QueryPlan(steps=["read orders"], tables=["orders"], notes="")

    def generate(self, system: str, prompt: str) -> GeneratedSql:
        self.generate_prompts.append(prompt)
        sql = self._sqls.pop(0) if self._sqls else "SELECT 1"
        return GeneratedSql(sql=sql, explanation="")


class FakeCursor:
    def __init__(self, columns, rows) -> None:
        self._columns = columns
        self._rows = rows

    def keys(self):
        return self._columns

    def fetchall(self):
        return self._rows


class FakeConn:
    def __init__(self, db) -> None:
        self._db = db

    def exec_driver_sql(self, statement):
        sql = str(statement)
        self._db.executed.append(sql)
        error = self._db.errors.pop(0) if self._db.errors else None
        if error is not None:
            raise ProgrammingError(sql, {}, Exception(error))
        return FakeCursor(["id"], [[1], [2]])


class FakeDb:
    """Fails with the scripted errors in order, then succeeds."""

    def __init__(self, errors: list[str | None]) -> None:
        self.errors = list(errors)
        self.executed: list[str] = []

    @contextmanager
    def readonly_connection(self):
        yield FakeConn(self)


def build(llm, db, max_retries: int = 3):
    deps = AgentDeps(
        llm=llm,
        schema_ddl="CREATE TABLE orders (id integer);",
        db=db,
        validator=SqlValidator(max_rows=1000),
        max_retries=max_retries,
    )
    return build_graph(deps)


def run(graph, question: str = "how many orders?"):
    return graph.invoke(initial_state(question), config={"recursion_limit": 40})


# -- claim 1: the DB error reaches the repair prompt ------------------------


def test_db_error_is_fed_back_into_generate() -> None:
    llm = FakeLlm(["SELECT revenu FROM orders", "SELECT id FROM orders"])
    db = FakeDb(errors=['column "revenu" does not exist', None])

    final = run(build(llm, db))

    assert final["status"] == "succeeded"
    assert len(llm.generate_prompts) == 2, "the loop did not retry"

    # The actual assertion. Without this the node could be regenerating from
    # identical inputs and everything else would still look correct.
    assert "revenu" in llm.generate_prompts[1]
    assert "does not exist" in llm.generate_prompts[1]
    assert "SELECT revenu FROM orders" in llm.generate_prompts[1]

    # ...and the first prompt must NOT contain repair text.
    assert "FAILED" not in llm.generate_prompts[0]


def test_schema_errors_route_back_to_plan() -> None:
    """A missing relation means the plan is wrong; regenerating SQL from the
    same plan would produce the same bad table forever."""
    llm = FakeLlm(["SELECT id FROM ordrs", "SELECT id FROM orders"])
    db = FakeDb(errors=['relation "ordrs" does not exist', None])

    final = run(build(llm, db))

    assert final["status"] == "succeeded"
    assert len(llm.plan_prompts) == 2, "schema error did not trigger a replan"


# -- claim 2: the guardrail reason reaches the repair prompt ----------------


@pytest.mark.parametrize(
    "rejected_sql",
    [
        # Root-type check fires: reason is "only read-only queries are allowed".
        "DELETE FROM orders",
        # Tree walk fires: roots as SELECT, reason is "DELETE is not allowed".
        "WITH d AS (DELETE FROM orders RETURNING *) SELECT * FROM d",
    ],
)
def test_validation_rejection_is_fed_back_into_generate(rejected_sql: str) -> None:
    llm = FakeLlm([rejected_sql, "SELECT id FROM orders"])
    db = FakeDb(errors=[None])

    # Ask the validator what it would say, rather than hardcoding a message
    # that drifts the moment the reason strings change.
    expected_reason = SqlValidator(max_rows=1000).validate(rejected_sql).reason

    final = run(build(llm, db))

    assert final["status"] == "succeeded"
    assert len(llm.generate_prompts) == 2
    assert expected_reason in llm.generate_prompts[1]
    assert "guardrail rejected it because" in llm.generate_prompts[1]

    # The rejected statement must never have reached the database.
    assert not any("DELETE" in s.upper() for s in db.executed)


def test_execute_only_ever_runs_validated_sql() -> None:
    """The LIMIT the guardrail injected must be what actually runs."""
    llm = FakeLlm(["SELECT id FROM orders"])
    db = FakeDb(errors=[None])

    final = run(build(llm, db))

    assert final["generated_sql"] == "SELECT id FROM orders"
    assert final["safe_sql"] == "SELECT id FROM orders LIMIT 1000"
    assert db.executed == ["SELECT id FROM orders LIMIT 1000"]


# -- claim 3: the loop is bounded ------------------------------------------


def test_loop_gives_up_and_reports_failure() -> None:
    llm = FakeLlm(["SELECT bad FROM orders"] * 10)
    db = FakeDb(errors=['column "bad" does not exist'] * 10)

    final = run(build(llm, db, max_retries=3))

    assert final["status"] == "failed"
    assert final["retry_count"] == 4  # 3 repairs + the one that gave up
    assert "gave up after 3 attempts" in final["failure_reason"]
    assert final["execution_result"] is None


def test_trace_records_every_attempt() -> None:
    """The append reducer on `attempts` is what makes the frontend trace real;
    a last-write-wins field would only ever show the final attempt."""
    llm = FakeLlm(["DELETE FROM orders", "SELECT bad FROM orders", "SELECT id FROM orders"])
    db = FakeDb(errors=['column "bad" does not exist', None])

    final = run(build(llm, db))
    attempts = final["attempts"]

    assert final["status"] == "succeeded"
    # validate-reject, validate-ok, execute-fail, validate-ok, execute-ok
    assert [a["stage"] for a in attempts] == [
        "validate",
        "validate",
        "execute",
        "validate",
        "execute",
    ]
    assert [a["ok"] for a in attempts] == [False, True, False, True, True]


@pytest.mark.parametrize("bad_sql", ["SELECT 1; DROP TABLE orders", "SELECT * INTO x FROM orders"])
def test_guardrail_blocks_reach_the_database(bad_sql: str) -> None:
    llm = FakeLlm([bad_sql, "SELECT id FROM orders"])
    db = FakeDb(errors=[None])

    run(build(llm, db))

    assert db.executed == ["SELECT id FROM orders LIMIT 1000"]
