-- Single source of truth for analysis assumptions (see docs/02_decisions_and_kpis.md).
-- Change a value here, rebuild, and every view and test follows.
DROP TABLE IF EXISTS params;
CREATE TABLE params (
    window_start    TEXT NOT NULL,    -- D2: first day of analysis window
    window_end      TEXT NOT NULL,    -- D2: last day of analysis window
    cutoff_date     TEXT NOT NULL,    -- D5: data cutoff for recency / time splits
    inactivity_days INTEGER NOT NULL, -- seller-failure definition A horizon
    cogs_rate       REAL NOT NULL,    -- D4: assumed cost-of-goods rate for profit proxy P2
    observation_end TEXT NOT NULL,    -- last delivery date observed in the extract (censoring point)
    late_model_end  TEXT NOT NULL     -- D6: last purchase date usable for the late-delivery model
);
INSERT INTO params
SELECT '2017-01-01', '2018-08-31', '2018-08-31', 90, 0.65,
       (SELECT MAX(date(order_delivered_customer_date)) FROM raw_orders),
       '2018-05-31';
