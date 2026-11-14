-- Dashboard time-range filters (Overview, Patterns)
CREATE INDEX IF NOT EXISTS idx_clean_txn_ts ON clean_transactions (txn_ts);

-- Account drill-down and per-sender features
CREATE INDEX IF NOT EXISTS idx_clean_sender_ts ON clean_transactions (sender_upi, txn_ts);

-- Money flowing into an account (mule analysis)
CREATE INDEX IF NOT EXISTS idx_clean_receiver_ts ON clean_transactions (receiver_upi, txn_ts);

-- Alerts queue: open alerts, highest risk first
CREATE INDEX IF NOT EXISTS idx_alerts_status_risk ON alerts (status, risk_score DESC);

-- "Rules triggered (any of)" filter
CREATE INDEX IF NOT EXISTS idx_alerts_rules ON alerts USING GIN (rules_triggered);