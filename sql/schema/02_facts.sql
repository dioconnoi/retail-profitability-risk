-- Fact tables at explicit grains. Derived order flags are computed once here.

-- Index the raw join key first: the has_items EXISTS below would otherwise scan
-- raw_order_items once per order (~99k x 112k rows).
CREATE INDEX IF NOT EXISTS ix_raw_items_order ON raw_order_items(order_id);

DROP TABLE IF EXISTS fact_orders;
CREATE TABLE fact_orders (                       -- grain: 1 row per order
    order_id       TEXT PRIMARY KEY,
    customer_id    TEXT NOT NULL,
    order_status   TEXT NOT NULL,
    purchase_ts    TEXT NOT NULL,
    approved_ts    TEXT,
    carrier_ts     TEXT,
    delivered_ts   TEXT,
    estimated_ts   TEXT NOT NULL,
    purchase_month TEXT NOT NULL,                -- 'YYYY-MM'
    in_window      INTEGER NOT NULL,             -- D2
    is_delivered   INTEGER NOT NULL,
    is_failed      INTEGER NOT NULL,             -- canceled or unavailable
    has_items      INTEGER NOT NULL,
    delivery_days  REAL,                         -- purchase -> customer, delivered orders only
    is_late        INTEGER,                      -- NULL when not evaluable (no delivery date)
    days_late      INTEGER,                      -- 0 when on time, NULL when not evaluable
    is_overdue_open INTEGER NOT NULL             -- still undelivered (not failed) and past its promised date at observation_end
);
INSERT INTO fact_orders
SELECT o.order_id, o.customer_id, o.order_status,
       o.order_purchase_timestamp, o.order_approved_at,
       o.order_delivered_carrier_date, o.order_delivered_customer_date,
       o.order_estimated_delivery_date,
       substr(o.order_purchase_timestamp, 1, 7),
       date(o.order_purchase_timestamp) BETWEEN p.window_start AND p.window_end,
       o.order_status = 'delivered',
       o.order_status IN ('canceled', 'unavailable'),
       EXISTS (SELECT 1 FROM raw_order_items i WHERE i.order_id = o.order_id),
       CASE WHEN o.order_status = 'delivered' AND o.order_delivered_customer_date IS NOT NULL
            THEN julianday(o.order_delivered_customer_date) - julianday(o.order_purchase_timestamp) END,
       -- Late = delivered on a later CALENDAR DAY than promised. The estimate is a date
       -- (00:00:00), so a same-day delivery must not count as late.
       CASE WHEN o.order_status = 'delivered' AND o.order_delivered_customer_date IS NOT NULL
            THEN date(o.order_delivered_customer_date) > date(o.order_estimated_delivery_date) END,
       CASE WHEN o.order_status = 'delivered' AND o.order_delivered_customer_date IS NOT NULL
            THEN MAX(0, CAST(julianday(date(o.order_delivered_customer_date))
                           - julianday(date(o.order_estimated_delivery_date)) AS INTEGER)) END,
       -- Censoring guard: 1,699 in-window orders never reached 'delivered'. They are NULL in is_late,
       -- so plain late rates are conditional on delivery and slightly optimistic. Track them here.
       o.order_status NOT IN ('delivered', 'canceled', 'unavailable')
         AND date(o.order_estimated_delivery_date) < p.observation_end
FROM raw_orders o CROSS JOIN params p;

DROP TABLE IF EXISTS fact_order_items;
CREATE TABLE fact_order_items (                  -- grain: 1 row per item; REVENUE LIVES HERE
    order_id      TEXT NOT NULL,
    order_item_id INTEGER NOT NULL,
    product_id    TEXT NOT NULL,
    seller_id     TEXT NOT NULL,
    price         REAL NOT NULL,
    freight_value REAL NOT NULL,
    PRIMARY KEY (order_id, order_item_id)
);
INSERT INTO fact_order_items
SELECT order_id, order_item_id, product_id, seller_id, price, freight_value FROM raw_order_items;

DROP TABLE IF EXISTS fact_reviews;
CREATE TABLE fact_reviews (                      -- grain: 1 row per order (latest review wins)
    order_id     TEXT PRIMARY KEY,
    review_id    TEXT NOT NULL,
    review_score INTEGER NOT NULL,
    is_low       INTEGER NOT NULL,               -- score <= 2
    answered_ts  TEXT                            -- when the score became known (as-of features must use this)
);
INSERT INTO fact_reviews
SELECT order_id, review_id, review_score, review_score <= 2, review_answer_timestamp
FROM (
    SELECT r.*,
           ROW_NUMBER() OVER (PARTITION BY order_id
                              ORDER BY review_answer_timestamp DESC,
                                       review_creation_date DESC,
                                       review_id) AS rn
    FROM raw_order_reviews r
)
WHERE rn = 1;

CREATE INDEX ix_orders_window  ON fact_orders(in_window, is_delivered);
CREATE INDEX ix_orders_month   ON fact_orders(purchase_month);
CREATE INDEX ix_orders_cust    ON fact_orders(customer_id);
CREATE INDEX ix_items_seller   ON fact_order_items(seller_id);
CREATE INDEX ix_items_product  ON fact_order_items(product_id);
