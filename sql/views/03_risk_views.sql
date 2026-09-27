-- Operational risk (Q3) and seller KPIs (Q4 input). Bases: fact_order_risk, fact_seller_orders.

DROP VIEW IF EXISTS v_risk_monthly;
CREATE VIEW v_risk_monthly AS
WITH d AS (
    SELECT purchase_month,
           COUNT(*)                                          AS delivered_orders,
           SUM(revenue)                                      AS revenue,
           COUNT(is_late)                                    AS late_evaluable,
           SUM(is_late)                                      AS late_orders,
           SUM(CASE WHEN is_late = 1 THEN revenue END)       AS late_revenue,
           COUNT(review_score)                               AS reviewed_orders,
           SUM(is_low)                                       AS low_review_orders,
           SUM(CASE WHEN is_low = 1 THEN revenue END)        AS low_review_revenue,
           SUM(CASE WHEN at_risk = 1 THEN revenue END)       AS at_risk_revenue,   -- union, no double count
           AVG(review_score)                                 AS avg_review
    FROM fact_order_risk GROUP BY purchase_month
),
f AS (   -- leakage: value of items on canceled/unavailable orders (never booked as revenue)
    SELECT o.purchase_month,
           COUNT(DISTINCT o.order_id) AS failed_orders_with_items,
           SUM(i.price)               AS lost_revenue
    FROM fact_orders o JOIN fact_order_items i ON i.order_id = o.order_id
    WHERE o.in_window = 1 AND o.is_failed = 1
    GROUP BY o.purchase_month
),
a AS (   -- all in-window orders, for the cancellation rate denominator
    SELECT purchase_month, COUNT(*) AS all_orders, SUM(is_failed) AS failed_orders
    FROM fact_orders WHERE in_window = 1 GROUP BY purchase_month
)
SELECT d.purchase_month, d.delivered_orders, ROUND(d.revenue, 2) AS revenue,
       ROUND(d.late_orders * 1.0 / d.late_evaluable, 4)        AS late_rate,
       ROUND(d.low_review_orders * 1.0 / d.reviewed_orders, 4) AS low_review_rate,
       ROUND(d.avg_review, 3)                                  AS avg_review,
       ROUND(COALESCE(d.late_revenue, 0), 2)                   AS late_revenue,
       ROUND(COALESCE(d.low_review_revenue, 0), 2)             AS low_review_revenue,
       ROUND(COALESCE(d.at_risk_revenue, 0), 2)                AS at_risk_revenue,
       ROUND(COALESCE(d.at_risk_revenue, 0) / d.revenue, 4)    AS at_risk_pct,
       a.all_orders, a.failed_orders,
       ROUND(a.failed_orders * 1.0 / a.all_orders, 4)          AS cancellation_rate,
       ROUND(COALESCE(f.lost_revenue, 0), 2)                   AS lost_revenue
FROM d JOIN a USING (purchase_month) LEFT JOIN f USING (purchase_month);

DROP VIEW IF EXISTS v_late_rates;
-- Late rate is conditional on delivery; the adjusted rate also counts orders that were never
-- delivered although their promised date passed before the extract ended (censoring).
CREATE VIEW v_late_rates AS
SELECT ROUND(SUM(is_late) * 1.0 / COUNT(is_late), 4)                                       AS late_rate_delivered,
       ROUND((SUM(is_late) + SUM(is_overdue_open)) * 1.0 / (COUNT(is_late) + SUM(is_overdue_open)), 4) AS late_rate_censoring_adjusted,
       COUNT(is_late)                                                                       AS delivered_evaluable,
       SUM(is_overdue_open)                                                                 AS overdue_open_orders
FROM fact_orders WHERE in_window = 1;

DROP VIEW IF EXISTS v_seller_perf;
-- DESCRIPTIVE only (whole-window aggregates). NOT a feature source for models: it includes the
-- label period. ML features are rebuilt as-of a cutoff in Python. cancellation_rate counts only
-- failed orders that have items (see fact_seller_orders), so it understates the platform rate.
CREATE VIEW v_seller_perf AS
SELECT so.seller_id,
       s.state AS seller_state,
       COUNT(*)                                          AS orders_all,
       SUM(so.is_delivered)                              AS orders_delivered,
       SUM(so.is_failed)                                 AS orders_failed,
       ROUND(SUM(CASE WHEN so.is_delivered = 1 THEN so.price END), 2) AS gross_revenue,
       ROUND(SUM(so.is_failed) * 1.0 / COUNT(*), 4)      AS cancellation_rate,
       ROUND(SUM(so.is_late) * 1.0 / COUNT(so.is_late), 4)  AS late_rate,
       ROUND(AVG(so.review_score), 3)                    AS avg_review,
       ROUND(SUM(so.is_low) * 1.0 / COUNT(so.review_score), 4) AS low_review_rate,
       MIN(date(so.purchase_ts))                         AS first_order_date,
       MAX(date(so.purchase_ts))                         AS last_order_date,
       CAST(julianday(p.cutoff_date) - julianday(MAX(date(so.purchase_ts))) AS INTEGER) AS days_since_last_order,
       COUNT(DISTINCT so.purchase_month)                 AS active_months
FROM fact_seller_orders so
JOIN dim_seller s ON s.seller_id = so.seller_id
CROSS JOIN params p
GROUP BY so.seller_id;
