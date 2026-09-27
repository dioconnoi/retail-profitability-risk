-- Profitability KPIs (Q1) and demand (Q2 input).

DROP VIEW IF EXISTS v_kpi_monthly;
CREATE VIEW v_kpi_monthly AS
WITH m AS (
    SELECT purchase_month,
           COUNT(DISTINCT order_id)           AS orders,
           COUNT(*)                           AS items,
           COUNT(DISTINCT customer_unique_id) AS customers,
           COUNT(DISTINCT seller_id)          AS active_sellers,
           SUM(price)                         AS gross_revenue,
           SUM(freight_value)                 AS freight,
           SUM(p1_net_of_freight)             AS p1_net_of_freight,
           SUM(p2_assumed_margin)             AS p2_assumed_margin
    FROM fact_sales
    GROUP BY purchase_month
)
SELECT purchase_month, orders, items, customers, active_sellers,
       ROUND(gross_revenue, 2)      AS gross_revenue,
       ROUND(freight, 2)            AS freight,
       ROUND(p1_net_of_freight, 2)  AS p1_net_of_freight,
       ROUND(p2_assumed_margin, 2)  AS p2_assumed_margin,
       ROUND(gross_revenue * 1.0 / orders, 2)      AS aov,
       ROUND(items * 1.0 / orders, 3)              AS items_per_order,
       ROUND(freight / gross_revenue, 4)           AS freight_ratio,
       ROUND(p2_assumed_margin / gross_revenue, 4) AS p2_margin_pct,
       ROUND(gross_revenue / LAG(gross_revenue) OVER (ORDER BY purchase_month) - 1, 4) AS revenue_mom,
       CASE WHEN COUNT(*) OVER (ORDER BY purchase_month ROWS BETWEEN 2 PRECEDING AND CURRENT ROW) = 3
            THEN ROUND(AVG(gross_revenue) OVER (ORDER BY purchase_month
                       ROWS BETWEEN 2 PRECEDING AND CURRENT ROW), 2) END               AS revenue_ma3  -- NULL until 3 months exist
FROM m;

DROP VIEW IF EXISTS v_kpi_summary;
CREATE VIEW v_kpi_summary AS
WITH cust AS (
    SELECT customer_unique_id, COUNT(DISTINCT order_id) AS n_orders
    FROM fact_sales GROUP BY customer_unique_id
)
SELECT (SELECT COUNT(DISTINCT order_id) FROM fact_sales)         AS orders,
       (SELECT ROUND(SUM(price), 2) FROM fact_sales)             AS gross_revenue,
       (SELECT ROUND(SUM(freight_value), 2) FROM fact_sales)     AS freight,
       (SELECT ROUND(SUM(p1_net_of_freight), 2) FROM fact_sales) AS p1_net_of_freight,
       (SELECT ROUND(SUM(p2_assumed_margin), 2) FROM fact_sales) AS p2_assumed_margin,
       COUNT(*)                                                    AS customers,
       ROUND(AVG(n_orders >= 2), 4)                                AS repeat_rate
FROM cust;

DROP VIEW IF EXISTS v_category_perf;
CREATE VIEW v_category_perf AS
WITH c AS (
    SELECT category_en,
           COUNT(DISTINCT order_id)   AS orders,
           SUM(price)                 AS gross_revenue,
           SUM(freight_value)         AS freight,
           SUM(p1_net_of_freight)     AS p1_net_of_freight,
           SUM(p2_assumed_margin)     AS p2_assumed_margin
    FROM fact_sales GROUP BY category_en
)
SELECT category_en, orders,
       ROUND(gross_revenue, 2)     AS gross_revenue,
       ROUND(p1_net_of_freight, 2) AS p1_net_of_freight,
       ROUND(p2_assumed_margin, 2) AS p2_assumed_margin,
       ROUND(freight / gross_revenue, 4)           AS freight_ratio,
       ROUND(p2_assumed_margin / gross_revenue, 4) AS p2_margin_pct,
       ROUND(gross_revenue * 1.0 / SUM(gross_revenue) OVER (), 6) AS revenue_share,
       ROUND(SUM(gross_revenue) OVER (ORDER BY gross_revenue DESC
             RANGE UNBOUNDED PRECEDING) * 1.0 / SUM(gross_revenue) OVER (), 6) AS cum_share,
       RANK() OVER (ORDER BY gross_revenue DESC) AS revenue_rank
FROM c;

DROP VIEW IF EXISTS v_state_perf;
-- Demand region = customer state; supply region = seller state (two lenses on "region").
CREATE VIEW v_state_perf AS
SELECT 'customer' AS lens, customer_state AS state,
       COUNT(DISTINCT order_id) AS orders,
       ROUND(SUM(price), 2)     AS gross_revenue,
       ROUND(SUM(p2_assumed_margin), 2) AS p2_assumed_margin,
       ROUND(SUM(freight_value) / SUM(price), 4) AS freight_ratio,
       ROUND(SUM(price) * 1.0 / SUM(SUM(price)) OVER (), 6) AS revenue_share
FROM fact_sales GROUP BY customer_state
UNION ALL
SELECT 'seller', seller_state,
       COUNT(DISTINCT order_id), ROUND(SUM(price), 2), ROUND(SUM(p2_assumed_margin), 2),
       ROUND(SUM(freight_value) / SUM(price), 4),
       ROUND(SUM(price) * 1.0 / SUM(SUM(price)) OVER (), 6)
FROM fact_sales GROUP BY seller_state;

DROP VIEW IF EXISTS v_concentration;
-- HHI = sum(share^2) x 10,000. Effective N = 1/sum(share^2). Computed per dimension.
CREATE VIEW v_concentration AS
WITH x AS (
    SELECT 'seller' AS dimension, seller_id AS entity, SUM(price) AS rev FROM fact_sales GROUP BY seller_id
    UNION ALL
    SELECT 'category', category_en, SUM(price) FROM fact_sales GROUP BY category_en
    UNION ALL
    SELECT 'customer_state', customer_state, SUM(price) FROM fact_sales GROUP BY customer_state
),
s AS (
    SELECT dimension, entity,
           rev * 1.0 / SUM(rev) OVER (PARTITION BY dimension)           AS share,
           ROW_NUMBER() OVER (PARTITION BY dimension ORDER BY rev DESC) AS rk,
           COUNT(*) OVER (PARTITION BY dimension)                       AS n
    FROM x
)
SELECT dimension,
       MAX(n)                                                AS n_entities,
       ROUND(SUM(share * share) * 10000, 1)                  AS hhi,
       ROUND(1.0 / SUM(share * share), 1)                    AS effective_n,
       ROUND(SUM(CASE WHEN rk <= 10 THEN share END), 4)      AS top10_share,
       ROUND(SUM(CASE WHEN rk <= n * 0.2 THEN share END), 4) AS top20pct_share
FROM s GROUP BY dimension;
