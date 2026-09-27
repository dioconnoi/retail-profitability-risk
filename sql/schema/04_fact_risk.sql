-- Materialised order-level risk bases (same reasoning as fact_sales: computed once at build).

DROP TABLE IF EXISTS fact_seller_orders;
-- (order, seller) grain over ALL in-window orders with items, any status.
-- Multi-seller orders (1,274 in window) appear once per seller, so order-level flags are attributed
-- to every seller on the order. Orders with no items (all 'unavailable' ones, some canceled) cannot
-- be attributed to a seller: seller cancellation rates therefore UNDERSTATE the platform rate.
CREATE TABLE fact_seller_orders (
    order_id       TEXT NOT NULL,
    seller_id      TEXT NOT NULL,
    purchase_ts    TEXT NOT NULL,
    purchase_month TEXT NOT NULL,
    is_delivered   INTEGER NOT NULL,
    is_failed      INTEGER NOT NULL,
    is_late        INTEGER,
    days_late      INTEGER,
    review_score   INTEGER,
    is_low         INTEGER,
    price          REAL NOT NULL,
    freight_value  REAL NOT NULL,
    PRIMARY KEY (order_id, seller_id)
);
INSERT INTO fact_seller_orders
SELECT i.order_id, i.seller_id, o.purchase_ts, o.purchase_month,
       o.is_delivered, o.is_failed, o.is_late, o.days_late,
       r.review_score, r.is_low,
       SUM(i.price), SUM(i.freight_value)
FROM fact_order_items i
JOIN fact_orders o       ON o.order_id = i.order_id
LEFT JOIN fact_reviews r ON r.order_id = i.order_id
WHERE o.in_window = 1
GROUP BY i.order_id, i.seller_id;
CREATE INDEX ix_so_seller ON fact_seller_orders(seller_id);
CREATE INDEX ix_so_month  ON fact_seller_orders(purchase_month);

DROP TABLE IF EXISTS fact_order_risk;
-- Order grain: delivered, in-window, with items. Base for revenue-at-risk and model targets.
CREATE TABLE fact_order_risk (
    order_id       TEXT PRIMARY KEY,
    customer_id    TEXT NOT NULL,
    purchase_ts    TEXT NOT NULL,
    purchase_month TEXT NOT NULL,
    revenue        REAL NOT NULL,
    freight_value  REAL NOT NULL,
    is_late        INTEGER,
    days_late      INTEGER,
    delivery_days  REAL,
    review_score   INTEGER,
    is_low         INTEGER,
    at_risk        INTEGER NOT NULL    -- late OR 1-2 star review (union: no double counting)
);
INSERT INTO fact_order_risk
SELECT o.order_id, o.customer_id, o.purchase_ts, o.purchase_month,
       SUM(i.price), SUM(i.freight_value),
       o.is_late, o.days_late, o.delivery_days,
       r.review_score, r.is_low,
       COALESCE(o.is_late, 0) = 1 OR COALESCE(r.is_low, 0) = 1
FROM fact_orders o
JOIN fact_order_items i  ON i.order_id = o.order_id
LEFT JOIN fact_reviews r ON r.order_id = o.order_id
WHERE o.in_window = 1 AND o.is_delivered = 1
GROUP BY o.order_id;
CREATE INDEX ix_or_month ON fact_order_risk(purchase_month);
