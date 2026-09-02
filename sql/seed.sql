-- Deterministic-ish demo data. Enough rows that aggregations are non-trivial
-- and LIMIT enforcement is observable.

INSERT INTO regions (name, country) VALUES
    ('North America', 'US'), ('EMEA', 'DE'), ('APAC', 'SG'), ('LATAM', 'BR')
ON CONFLICT (name) DO NOTHING;

INSERT INTO products (sku, name, category, unit_cost, list_price) VALUES
    ('SKU-001', 'Standard Widget',   'widgets',    4.50,  12.00),
    ('SKU-002', 'Premium Widget',    'widgets',   11.00,  34.00),
    ('SKU-003', 'Widget Refill Pack','widgets',    1.20,   5.00),
    ('SKU-004', 'Basic Gadget',      'gadgets',   18.00,  49.00),
    ('SKU-005', 'Pro Gadget',        'gadgets',   42.00, 129.00),
    ('SKU-006', 'Gadget Mount',      'accessories',3.00,  15.00),
    ('SKU-007', 'Carry Case',        'accessories',6.75,  24.00),
    ('SKU-008', 'Extended Warranty', 'services',   0.00,  79.00)
ON CONFLICT (sku) DO NOTHING;

INSERT INTO customers (name, email, region_id, segment, signed_up_at)
SELECT
    'Customer ' || g,
    'customer' || g || '@example.com',
    (g % 4) + 1,
    (ARRAY['smb','mid_market','enterprise'])[(g % 3) + 1],
    now() - (g % 500) * INTERVAL '1 day'
FROM generate_series(1, 300) AS g
ON CONFLICT (email) DO NOTHING;

INSERT INTO orders (customer_id, status, placed_at, shipped_at)
SELECT
    (g % 300) + 1,
    (ARRAY['pending','shipped','delivered','delivered','refunded'])[(g % 5) + 1],
    now() - (g % 400) * INTERVAL '1 day',
    CASE WHEN g % 5 IN (1, 2, 3)
         THEN now() - (g % 400) * INTERVAL '1 day' + INTERVAL '2 days'
         ELSE NULL END
FROM generate_series(1, 2000) AS g;

INSERT INTO order_items (order_id, product_id, quantity, unit_price)
SELECT
    o.id,
    ((o.id + i) % 8) + 1,
    ((o.id + i) % 4) + 1,
    p.list_price * (1 - ((o.id % 3) * 0.05))
FROM orders o
CROSS JOIN generate_series(0, 2) AS i
JOIN products p ON p.id = ((o.id + i) % 8) + 1;
