-- Black Rhino Liquor Store catalogue import
-- Generated from the provided stock-taking pages.
-- Prices are KES. Stock is initialized to 0 because stock counts were not supplied.
-- Size normalization: 1/2 and HALF = 500ml; 1/4 = 250ml.
-- Run migrations/schema first, then execute this file.

BEGIN;

CREATE TEMP TABLE br_catalog_import (
    product_name text NOT NULL,
    variant_name text NOT NULL,
    size text NOT NULL,
    unit text NOT NULL,
    sku text NOT NULL,
    brand text NOT NULL,
    category text NOT NULL,
    selling_price numeric(12,2) NOT NULL
) ON COMMIT DROP;

\copy br_catalog_import FROM 'scripts/catalog.csv' WITH (FORMAT csv, HEADER true, ENCODING 'UTF8');

INSERT INTO categories (name, is_active)
SELECT DISTINCT category, true FROM br_catalog_import
ON CONFLICT (name) DO UPDATE SET is_active = true;

INSERT INTO brands (name, is_active)
SELECT DISTINCT brand, true FROM br_catalog_import WHERE brand <> ''
ON CONFLICT (name) DO UPDATE SET is_active = true;

INSERT INTO products (name, brand_id, category_id, is_active, created_at)
SELECT DISTINCT
    c.product_name,
    b.id,
    cat.id,
    true,
    NOW()
FROM br_catalog_import c
JOIN categories cat ON cat.name = c.category
LEFT JOIN brands b ON b.name = c.brand
WHERE NOT EXISTS (
    SELECT 1 FROM products p
    WHERE p.name = c.product_name AND p.category_id = cat.id
);

INSERT INTO product_variants
    (product_id, size, unit, sku, cost_price, selling_price, wholesale_price, reorder_level, is_active)
SELECT
    p.id,
    c.size,
    c.unit,
    c.sku,
    0,
    c.selling_price,
    0,
    5,
    true
FROM br_catalog_import c
JOIN products p ON p.name = c.product_name
ON CONFLICT (sku) DO UPDATE
SET size = EXCLUDED.size,
    unit = EXCLUDED.unit,
    selling_price = EXCLUDED.selling_price,
    is_active = true;

INSERT INTO inventory (variant_id, quantity, reserved_quantity)
SELECT v.id, 0, 0
FROM br_catalog_import c
JOIN product_variants v ON v.sku = c.sku
ON CONFLICT (variant_id) DO NOTHING;

COMMIT;
