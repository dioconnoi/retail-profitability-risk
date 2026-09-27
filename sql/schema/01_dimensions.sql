-- Dimension tables: typed, deduplicated, English category names.

DROP TABLE IF EXISTS dim_category;
CREATE TABLE dim_category (
    category_pt TEXT PRIMARY KEY,
    category_en TEXT NOT NULL
);
INSERT INTO dim_category
SELECT product_category_name, product_category_name_english FROM raw_category_translation;
-- Audit finding: 2 categories used by products are absent from the translation file.
INSERT INTO dim_category VALUES
    ('pc_gamer', 'pc_gamer'),
    ('portateis_cozinha_e_preparadores_de_alimentos', 'portable_kitchen_food_preparers');

DROP TABLE IF EXISTS dim_product;
CREATE TABLE dim_product (
    product_id   TEXT PRIMARY KEY,
    category_en  TEXT NOT NULL,      -- 'unknown' when the source category is NULL (610 products)
    weight_g     REAL,
    length_cm    REAL,
    height_cm    REAL,
    width_cm     REAL,
    photos_qty   INTEGER
);
INSERT INTO dim_product
SELECT p.product_id,
       COALESCE(c.category_en, 'unknown'),
       p.product_weight_g, p.product_length_cm, p.product_height_cm,
       p.product_width_cm, p.product_photos_qty
FROM raw_products p
LEFT JOIN dim_category c ON c.category_pt = p.product_category_name;

DROP TABLE IF EXISTS dim_seller;
CREATE TABLE dim_seller (
    seller_id  TEXT PRIMARY KEY,
    zip_prefix INTEGER,
    city       TEXT,
    state      TEXT
);
INSERT INTO dim_seller
SELECT seller_id, seller_zip_code_prefix, seller_city, seller_state FROM raw_sellers;

DROP TABLE IF EXISTS dim_customer;
CREATE TABLE dim_customer (
    customer_id        TEXT PRIMARY KEY,   -- one per ORDER in Olist
    customer_unique_id TEXT NOT NULL,      -- the real person; use this for repeat analysis
    zip_prefix         INTEGER,
    city               TEXT,
    state              TEXT
);
INSERT INTO dim_customer
SELECT customer_id, customer_unique_id, customer_zip_code_prefix, customer_city, customer_state
FROM raw_customers;

-- Geolocation has ~1M rows for ~19k zip prefixes: collapse to one centroid per prefix.
DROP TABLE IF EXISTS geo_zip;
CREATE TABLE geo_zip (
    zip_prefix INTEGER PRIMARY KEY,
    lat        REAL NOT NULL,
    lng        REAL NOT NULL
);
INSERT INTO geo_zip
SELECT geolocation_zip_code_prefix, AVG(geolocation_lat), AVG(geolocation_lng)
FROM raw_geolocation
GROUP BY geolocation_zip_code_prefix;

CREATE INDEX ix_customer_unique ON dim_customer(customer_unique_id);
CREATE INDEX ix_product_cat     ON dim_product(category_en);
