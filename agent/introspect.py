"""Read the live schema out of Postgres and render it as DDL for the prompt.

This is read once at startup and closed over by the nodes rather than carried in
graph state. State is serialized at every checkpoint, and the schema never
changes during a run -- putting it in state would write the whole DDL to the
checkpoint store on every step for no benefit.

If you later grow the schema past what fits comfortably in a prompt, this is the
seam where retrieval goes, and *then* the selected subset does belong in state
because it varies per question.
"""

from __future__ import annotations

from sqlalchemy import text

from api.database import Database

_COLUMNS_SQL = """
SELECT table_name, column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_schema = 'public'
ORDER BY table_name, ordinal_position
"""

_FKS_SQL = """
SELECT
    tc.table_name        AS from_table,
    kcu.column_name      AS from_column,
    ccu.table_name       AS to_table,
    ccu.column_name      AS to_column
FROM information_schema.table_constraints tc
JOIN information_schema.key_column_usage kcu
    ON tc.constraint_name = kcu.constraint_name
   AND tc.table_schema = kcu.table_schema
JOIN information_schema.constraint_column_usage ccu
    ON ccu.constraint_name = tc.constraint_name
   AND ccu.table_schema = tc.table_schema
WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_schema = 'public'
ORDER BY from_table, from_column
"""


def render_schema(db: Database) -> str:
    """Return a compact CREATE-TABLE-ish rendering of the public schema."""
    with db.readonly_connection() as conn:
        columns = conn.execute(text(_COLUMNS_SQL)).fetchall()
        fks = conn.execute(text(_FKS_SQL)).fetchall()

    tables: dict[str, list[str]] = {}
    for table_name, column_name, data_type, is_nullable in columns:
        null = "" if is_nullable == "YES" else " NOT NULL"
        tables.setdefault(table_name, []).append(f"    {column_name} {data_type}{null}")

    blocks: list[str] = []
    for table_name, cols in tables.items():
        blocks.append(f"CREATE TABLE {table_name} (\n" + ",\n".join(cols) + "\n);")

    if fks:
        rels = [
            f"-- {r.from_table}.{r.from_column} -> {r.to_table}.{r.to_column}" for r in fks
        ]
        blocks.append("-- Foreign keys\n" + "\n".join(rels))

    return "\n\n".join(blocks)
