-- Materialised revenue grain: delivered, in-window ORDER ITEMS (D2 + D3), with both profit
-- proxies (D4). Every KPI view reads from here, so the revenue definition lives in ONE place.
-- Materialised (not a view) because five-table joins were recomputed by every downstream view.
-- Changing params (window, COGS rate) requires a rebuild, which build_db.py always does.

DROP TABLE IF EXISTS fact_sales;
CREATE TABLE fact_sales (
    order_id            TEXT NOT NULL,
    order_item_id       INTEGER NOT NULL,
    seller_id           TEXT NOT NULL,
    product_id          TEXT NOT NULL,
    customer_id         TEXT NOT NULL,
    customer_unique_id  TEXT NOT NULL,
    purchase_month      TEXT NOT NULL,
    category_en         TEXT NOT NULL,
    seller_state        TEXT,
    customer_state      TEXT,
    price               REAL NOT NULL,
    freight_value       REAL NOT NULL,
    p1_net_of_freight   REAL NOT NULL,   -- price - freight
    p2_assumed_margin   REAL NOT NULL,   -- price x (1 - cogs_rate) - freight
    PRIMARY KEY (order_id, order_item_id)
);
INSERT INTO fact_sales
SELECT i.order_id, i.order_item_id, i.seller_id, i.product_id,
       o.customer_id, c.customer_unique_id, o.purchase_month,
       pr.category_en, s.state, c.state,
       i.price, i.freight_value,
       i.price - i.freight_value,
       i.price * (1 - p.cogs_rate) - i.freight_value
FROM fact_order_items i
JOIN fact_orders   o  ON o.order_id    = i.order_id
JOIN dim_customer  c  ON c.customer_id = o.customer_id
JOIN dim_product   pr ON pr.product_id = i.product_id
JOIN dim_seller    s  ON s.seller_id   = i.seller_id
CROSS JOIN params  p
WHERE o.in_window = 1 AND o.is_delivered = 1;

CREATE INDEX ix_sales_month    ON fact_sales(purchase_month);
CREATE INDEX ix_sales_seller   ON fact_sales(seller_id);
CREATE INDEX ix_sales_category ON fact_sales(category_en);
CREATE INDEX ix_sales_customer ON fact_sales(customer_unique_id);
