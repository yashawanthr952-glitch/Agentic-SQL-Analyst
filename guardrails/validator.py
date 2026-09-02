"""AST-based read-only SQL guardrail.

Why an AST and not a regex
--------------------------
A regex denylist operates on *characters*; Postgres operates on a *parse tree*.
Every place those two models diverge is an exploit, and the divergences are not
enumerable: ``DELETE/**/FROM t`` (comments are token separators),
``SELECT 'DROP TABLE x'`` (keyword inside a literal),
``$tag$ DROP TABLE x $tag$`` (dollar quoting with an arbitrary delimiter),
``WITH d AS (DELETE FROM t RETURNING *) SELECT * FROM d`` (the write is nested
and the statement reads as a SELECT), and ``SELECT * INTO exfil FROM users``
(creates a table while containing no forbidden keyword at all).

A denylist is unbounded because it enumerates badness. An allowlist over a
grammar is finite because it asserts the shape of what we accept. Parsing turns
"is this string dangerous?" into "is every node in this tree permitted?".

Failing closed is structural, not stylistic
-------------------------------------------
sqlglot is not the Postgres parser -- it approximates the grammar, and the two
will occasionally disagree. Rejecting anything we cannot parse confidently means
a disagreement surfaces as a *rejected valid query* (annoying, and recoverable
by the repair loop) rather than an *accepted dangerous one* (fatal). We are
choosing which direction our errors point.

This validator is layer 3 of three. See README "Defense in depth": the DB role
has no write grants (layer 1) and runs read-only transactions (layer 2).
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import Any

import sqlglot
from sqlglot import exp

logger = logging.getLogger(__name__)

# sqlglot logs a warning every time it falls back to a generic Command node.
# We treat Command as a rejection, so the warning is pure noise while the
# adversarial suite runs.
logging.getLogger("sqlglot").setLevel(logging.ERROR)


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    reason: str | None = None
    rewritten_sql: str | None = None
    limit_injected: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


# Any of these anywhere in the tree is fatal -- not just at the root. The CTE
# and SELECT..INTO cases are exactly why this must be a full walk.
FORBIDDEN_NODES: dict[type[exp.Expression], str] = {
    exp.Insert: "INSERT is not allowed",
    exp.Update: "UPDATE is not allowed",
    exp.Delete: "DELETE is not allowed",
    exp.Drop: "DROP is not allowed",
    exp.Create: "CREATE is not allowed",
    exp.Alter: "ALTER is not allowed",
    exp.TruncateTable: "TRUNCATE is not allowed",
    exp.Grant: "GRANT is not allowed",
    exp.Merge: "MERGE is not allowed",
    exp.Copy: "COPY is not allowed",
    exp.Into: "SELECT ... INTO creates a table and is not allowed",
    exp.Set: "SET is not allowed (it can disable our own safety settings)",
    exp.Use: "USE is not allowed",
    exp.Transaction: "explicit transaction control is not allowed",
    exp.Commit: "explicit transaction control is not allowed",
    exp.Rollback: "explicit transaction control is not allowed",
    exp.Attach: "ATTACH is not allowed",
    exp.Analyze: "ANALYZE is not allowed",
    exp.Refresh: "REFRESH is not allowed",
    # Command is the sqlglot fallback for any statement it does not model:
    # VACUUM, CALL, REINDEX, SECURITY LABEL, COPY ... TO PROGRAM and friends.
    # Rejecting it closes a whole class in one line, and it is the check people
    # most often forget.
    exp.Command: "statement type is not recognised as a read-only query",
}

# Read-only with respect to the *database*, dangerous with respect to
# everything else: filesystem access, outbound connections, SQL eval, and
# resource exhaustion that a row cap does not bound.
FORBIDDEN_FUNCTIONS: frozenset[str] = frozenset(
    {
        # SQL eval -- takes a query string and runs it. A SELECT-shaped eval hole.
        "query_to_xml",
        "query_to_xmlschema",
        "query_to_xml_and_xmlschema",
        # Filesystem
        "pg_read_file",
        "pg_read_binary_file",
        "pg_ls_dir",
        "pg_stat_file",
        "lo_import",
        "lo_export",
        # Outbound network
        "dblink",
        "dblink_exec",
        "dblink_connect",
        # Resource exhaustion -- holds a connection without returning rows
        "pg_sleep",
        "pg_sleep_for",
        "pg_sleep_until",
        # Session and privilege poking
        "set_config",
        "pg_terminate_backend",
        "pg_cancel_backend",
        "pg_reload_conf",
        "pg_rotate_logfile",
    }
)

# The only root node types that can be a read-only query.
ALLOWED_ROOTS: tuple[type[exp.Expression], ...] = (
    exp.Select,
    exp.Union,
    exp.Except,
    exp.Intersect,
    exp.Subquery,
    exp.Values,
)


class SqlValidator:
    """Validates and rewrites a single generated statement."""

    def __init__(self, max_rows: int = 1000, dialect: str = "postgres") -> None:
        self.max_rows = max_rows
        self.dialect = dialect

    def validate(self, sql: str) -> ValidationResult:
        """Return a ValidationResult. Never raises -- see the module docstring.

        A raised exception here would escape the LangGraph node and crash the
        graph, which means the repair loop would never run. Every failure has to
        come back as ok=False with a reason the repair node can hand to the LLM.
        """
        try:
            return self._validate(sql)
        except Exception as exc:  # noqa: BLE001 -- fail closed on anything
            logger.warning("validator rejected unparseable SQL: %s", exc)
            return ValidationResult(
                ok=False, reason=f"could not parse SQL as a read-only query: {exc}"
            )

    # -- internals ---------------------------------------------------------

    def _validate(self, sql: str) -> ValidationResult:
        if not sql or not sql.strip():
            return ValidationResult(ok=False, reason="empty statement")

        # 1. Parse to a LIST. parse_one() would silently take the first
        #    statement and drop "; DROP TABLE users" on the floor.
        expressions = [e for e in sqlglot.parse(sql, dialect=self.dialect) if e is not None]

        if not expressions:
            return ValidationResult(ok=False, reason="no statement found")
        if len(expressions) > 1:
            return ValidationResult(
                ok=False,
                reason=(
                    f"expected exactly 1 statement, found {len(expressions)} "
                    "(stacked statements are not allowed)"
                ),
            )

        tree = expressions[0]

        # 2. Root type allowlist. Note a CTE does not change the root: sqlglot
        #    hangs WITH off the Select args, so `WITH x AS (...) SELECT` still
        #    roots as Select. That is precisely why step 3 exists.
        if not isinstance(tree, ALLOWED_ROOTS):
            return ValidationResult(
                ok=False,
                reason=f"only read-only queries are allowed, got {type(tree).__name__.upper()}",
            )

        # 3. Walk the ENTIRE tree. This is the check that separates a real
        #    guardrail from a root-type check that a CTE walks straight past.
        for node in tree.walk():
            for forbidden, reason in FORBIDDEN_NODES.items():
                if isinstance(node, forbidden):
                    return ValidationResult(ok=False, reason=reason)

        # 4. Function denylist. Known functions parse to typed classes; unknown
        #    ones land in exp.Anonymous with .name set to the function name.
        for func in tree.find_all(exp.Func):
            name = func.name if isinstance(func, exp.Anonymous) else func.sql_name()
            if name and name.lower() in FORBIDDEN_FUNCTIONS:
                return ValidationResult(ok=False, reason=f"function {name}() is not allowed")

        # 5. Row cap.
        tree, limit_injected = self._enforce_limit(tree)

        # 6. Re-render from the AST rather than passing the original text
        #    through. Anything the parser did not understand cannot survive into
        #    execution -- normalisation is a free extra layer.
        return ValidationResult(
            ok=True,
            rewritten_sql=tree.sql(dialect=self.dialect),
            limit_injected=limit_injected,
        )

    def _enforce_limit(self, tree: exp.Expression) -> tuple[exp.Expression, bool]:
        """Inject or clamp LIMIT.

        Anything that is not a plain integer literal at or under the cap gets
        overwritten -- that covers FETCH FIRST, expressions and parameters
        without having to reason about each one separately.
        """
        existing = tree.args.get("limit")

        if isinstance(existing, exp.Limit):
            value = existing.expression
            if isinstance(value, exp.Literal) and value.is_int:
                if int(value.name) <= self.max_rows:
                    return tree, False

        return tree.limit(self.max_rows), True
