-- Demo analytics schema. Small enough that the whole DDL fits in the prompt,
-- wide enough that the planner has real joins and aggregations to reason about.

CREATE TABLE IF NOT EXISTS regions (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    country     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS customers (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    email       TEXT NOT NULL UNIQUE,
    region_id   INTEGER NOT NULL REFERENCES regions(id),
    segment     TEXT NOT NULL CHECK (segment IN ('smb', 'mid_market', 'enterprise')),
    signed_up_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS products (
    id          SERIAL PRIMARY KEY,
    sku         TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    category    TEXT NOT NULL,
    unit_cost   NUMERIC(10,2) NOT NULL,
    list_price  NUMERIC(10,2) NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    id          SERIAL PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    status      TEXT NOT NULL CHECK (status IN ('pending', 'shipped', 'delivered', 'refunded')),
    placed_at   TIMESTAMPTZ NOT NULL,
    shipped_at  TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS order_items (
    id          SERIAL PRIMARY KEY,
    order_id    INTEGER NOT NULL REFERENCES orders(id),
    product_id  INTEGER NOT NULL REFERENCES products(id),
    quantity    INTEGER NOT NULL CHECK (quantity > 0),
    unit_price  NUMERIC(10,2) NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_orders_customer ON orders(customer_id);
CREATE INDEX IF NOT EXISTS idx_orders_placed_at ON orders(placed_at);
CREATE INDEX IF NOT EXISTS idx_order_items_order ON order_items(order_id);
CREATE INDEX IF NOT EXISTS idx_order_items_product ON order_items(product_id);

-- Layer 1 of the read-only guardrail: the agent's role physically cannot write.
-- The AST validator in guardrails/ is layer 3; see README "Defense in depth".
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'analyst_ro') THEN
        CREATE ROLE analyst_ro LOGIN PASSWORD 'analyst_ro';
    END IF;
END
$$;

GRANT CONNECT ON DATABASE analytics TO analyst_ro;
GRANT USAGE ON SCHEMA public TO analyst_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO analyst_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO analyst_ro;

REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER
    ON ALL TABLES IN SCHEMA public FROM analyst_ro;
REVOKE CREATE ON SCHEMA public FROM analyst_ro;

-- Layer 2: even a hypothetical write that slipped past layers 1 and 3 aborts.
ALTER ROLE analyst_ro SET default_transaction_read_only = on;
