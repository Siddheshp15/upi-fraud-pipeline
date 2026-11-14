-- Layer 0: audit log of every pipeline run
CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id            SERIAL PRIMARY KEY,
    source_file       TEXT,
    started_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at       TIMESTAMPTZ,
    rows_in           INTEGER DEFAULT 0,
    rows_cleaned      INTEGER DEFAULT 0,
    rows_rejected     INTEGER DEFAULT 0,
    duplicates_removed INTEGER DEFAULT 0,
    alerts_created    INTEGER DEFAULT 0,
    duration_seconds  NUMERIC(10,2),
    status            TEXT DEFAULT 'running'
);

-- Layer 1: raw, untouched (everything is text on purpose)
CREATE TABLE IF NOT EXISTS raw_transactions (
    raw_id        BIGSERIAL PRIMARY KEY,
    run_id        INTEGER REFERENCES pipeline_runs(run_id),
    source_file   TEXT,
    txn_id        TEXT,
    "timestamp"   TEXT,
    sender_upi    TEXT,
    receiver_upi  TEXT,
    amount        TEXT,
    status        TEXT,
    device_id     TEXT,
    location      TEXT,
    ingested_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Layer 2: clean, typed, one row per txn_id
CREATE TABLE IF NOT EXISTS clean_transactions (
    txn_id        TEXT PRIMARY KEY,
    txn_ts        TIMESTAMPTZ NOT NULL,
    sender_upi    TEXT NOT NULL,
    receiver_upi  TEXT NOT NULL,
    amount        NUMERIC(12,2) NOT NULL CHECK (amount > 0),
    status        TEXT NOT NULL CHECK (status IN ('SUCCESS', 'FAILED')),
    device_id     TEXT,
    location      TEXT,
    run_id        INTEGER REFERENCES pipeline_runs(run_id),
    loaded_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Rows that failed validation, with the reason
CREATE TABLE IF NOT EXISTS rejected_rows (
    reject_id     BIGSERIAL PRIMARY KEY,
    run_id        INTEGER REFERENCES pipeline_runs(run_id),
    source_file   TEXT,
    raw_data      JSONB,
    reject_reason TEXT NOT NULL,
    rejected_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Per-user behaviour profile
CREATE TABLE IF NOT EXISTS user_features (
    upi_id          TEXT PRIMARY KEY,
    avg_amount      NUMERIC(12,2),
    txn_count       INTEGER,
    usual_device    TEXT,
    usual_location  TEXT,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Flagged transactions for analysts
CREATE TABLE IF NOT EXISTS alerts (
    alert_id        SERIAL PRIMARY KEY,
    txn_id          TEXT NOT NULL UNIQUE REFERENCES clean_transactions(txn_id),
    risk_score      NUMERIC(5,2) NOT NULL,
    rule_score      NUMERIC(5,2),
    anomaly_score   NUMERIC(5,2),
    rules_triggered TEXT[],
    status          TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'reviewed')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    reviewed_at     TIMESTAMPTZ
);

ALTER TABLE user_features ADD COLUMN IF NOT EXISTS median_amount NUMERIC(12,2);