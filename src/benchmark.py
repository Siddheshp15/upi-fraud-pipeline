"""Benchmark the dashboard queries on 1M+ rows, before and after indexes.
Everything happens in a separate 'perf' schema, so your real data is untouched.
  python -m src.benchmark            run the benchmark
  python -m src.benchmark --drop     delete the perf schema when you are done"""
import argparse
import json
import statistics
import time
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine

from src.config import DB_URL

REPORTS = Path("reports")
SINCE = "now() - interval '7 days'"

SCHEMA_SQL = """
DROP SCHEMA IF EXISTS perf CASCADE;
CREATE SCHEMA perf;
CREATE TABLE perf.clean_transactions (
    txn_id       TEXT,
    txn_ts       TIMESTAMPTZ NOT NULL,
    sender_upi   TEXT NOT NULL,
    receiver_upi TEXT NOT NULL,
    amount       NUMERIC(12,2) NOT NULL,
    status       TEXT NOT NULL,
    device_id    TEXT,
    location     TEXT
);
CREATE TABLE perf.alerts (
    alert_id        SERIAL,
    txn_id          TEXT NOT NULL,
    risk_score      NUMERIC(5,2) NOT NULL,
    rules_triggered TEXT[],
    status          TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL
);
"""

# rows are inserted in time order, like a real append-only feed
POPULATE_SQL = """
INSERT INTO perf.clean_transactions
    (txn_id, txn_ts, sender_upi, receiver_upi, amount, status, device_id, location)
SELECT 'P' || lpad(g::text, 10, '0'),
       ts,
       'user' || (1 + floor(r1 * 50000))::int || '@' || (ARRAY['oksbi','ybl','paytm','okhdfcbank'])[1 + floor(r2 * 4)::int],
       'user' || (1 + floor(r3 * 50000))::int || '@' || (ARRAY['oksbi','ybl','paytm','okhdfcbank'])[1 + floor(r4 * 4)::int],
       round((100 + power(r5, 3) * 20000)::numeric, 2),
       CASE WHEN r6 < 0.97 THEN 'SUCCESS' ELSE 'FAILED' END,
       'DEV' || (1 + floor(r1 * 50000))::int,
       (ARRAY['Mumbai','Delhi','Bengaluru','Chennai','Kolkata','Pune'])[1 + floor(r7 * 6)::int]
FROM (
    SELECT g, now() - random() * interval '30 days' AS ts,
           random() AS r1, random() AS r2, random() AS r3, random() AS r4,
           random() AS r5, random() AS r6, random() AS r7
    FROM generate_series(1, __N__) AS g
) s
ORDER BY ts;
"""

ALERTS_SQL = """
INSERT INTO perf.alerts (txn_id, risk_score, rules_triggered, status, created_at)
SELECT txn_id,
       round((100 * power(random(), 1.5))::numeric, 2),
       ARRAY[(ARRAY['velocity','amount_spike','new_payee_high','mule_fan_in','mule_outflow',
                    'odd_hours','failed_then_success','device_location_change','round_amount'])
             [1 + floor(random() * 9)::int]],
       CASE WHEN random() < 0.8 THEN 'open' ELSE 'reviewed' END,
       txn_ts
FROM perf.clean_transactions
WHERE random() < 0.02;
"""


def make_queries(rows):
    mid_id = "P" + str(rows // 2).zfill(10)
    return [
        ("Overview KPIs (7 days)", True, f"""
            SELECT count(*) AS txns, count(a.alert_id) AS flagged,
                   coalesce(sum(c.amount) FILTER (WHERE a.alert_id IS NOT NULL
                                                  AND c.status = 'SUCCESS'), 0) AS at_risk
            FROM clean_transactions c LEFT JOIN alerts a ON a.txn_id = c.txn_id
            WHERE c.txn_ts >= {SINCE}"""),
        ("Daily trend (7 days)", True, f"""
            SELECT date_trunc('day', c.txn_ts AT TIME ZONE 'Asia/Kolkata') AS day,
                   count(*) AS txns, count(a.alert_id) AS alerts
            FROM clean_transactions c LEFT JOIN alerts a ON a.txn_id = c.txn_id
            WHERE c.txn_ts >= {SINCE} GROUP BY 1 ORDER BY 1"""),
        ("Alerts queue: top 200 open", True, f"""
            SELECT a.alert_id, a.risk_score, a.rules_triggered, c.txn_id, c.txn_ts,
                   c.sender_upi, c.receiver_upi, c.amount
            FROM alerts a JOIN clean_transactions c ON c.txn_id = a.txn_id
            WHERE c.txn_ts >= {SINCE} AND a.status = 'open'
            ORDER BY a.risk_score DESC LIMIT 200"""),
        ("Alerts queue + rule filter", True, f"""
            SELECT a.alert_id, a.risk_score, a.rules_triggered, c.txn_id, c.txn_ts,
                   c.sender_upi, c.receiver_upi, c.amount
            FROM alerts a JOIN clean_transactions c ON c.txn_id = a.txn_id
            WHERE c.txn_ts >= {SINCE} AND a.status = 'open'
              AND a.rules_triggered && ARRAY['mule_fan_in']::text[]
            ORDER BY a.risk_score DESC LIMIT 200"""),
        ("Riskiest accounts (7 days)", True, f"""
            SELECT c.sender_upi, count(*) AS alerts, max(a.risk_score) AS max_risk
            FROM alerts a JOIN clean_transactions c ON c.txn_id = a.txn_id
            WHERE c.txn_ts >= {SINCE}
            GROUP BY 1 ORDER BY 2 DESC, 3 DESC LIMIT 15"""),
        ("Account lookup (analyst drill-down)", False, f"""
            SELECT txn_id, txn_ts, receiver_upi, amount, status
            FROM clean_transactions
            WHERE sender_upi = (SELECT sender_upi FROM clean_transactions
                                WHERE txn_id = '{mid_id}')
            ORDER BY txn_ts DESC LIMIT 50"""),
    ]


def load(conn, n):
    print(f"Creating perf schema and loading {n:,} rows (about a minute)...")
    conn.exec_driver_sql(SCHEMA_SQL)
    conn.exec_driver_sql(POPULATE_SQL.replace("__N__", str(n)))
    conn.exec_driver_sql(ALERTS_SQL)
    conn.exec_driver_sql("ALTER TABLE perf.clean_transactions ADD PRIMARY KEY (txn_id)")
    conn.exec_driver_sql("ALTER TABLE perf.alerts ADD PRIMARY KEY (alert_id)")
    conn.exec_driver_sql("ALTER TABLE perf.alerts ADD UNIQUE (txn_id)")
    conn.exec_driver_sql("VACUUM ANALYZE perf.clean_transactions")
    conn.exec_driver_sql("VACUUM ANALYZE perf.alerts")


def time_query(conn, sql, runs=7):
    conn.exec_driver_sql(sql).fetchall()          # warm-up run, not counted
    times = []
    for _ in range(runs):
        t0 = time.perf_counter()
        conn.exec_driver_sql(sql).fetchall()
        times.append((time.perf_counter() - t0) * 1000)
    return statistics.median(times)


def scan_summary(conn, sql):
    raw = conn.exec_driver_sql("EXPLAIN (FORMAT JSON) " + sql).fetchone()[0]
    if isinstance(raw, str):
        raw = json.loads(raw)
    found = []

    def walk(node):
        kind = node["Node Type"]
        if "Scan" in kind and "Relation Name" in node:
            found.append(f"{kind} on {node['Relation Name']}")
        for child in node.get("Plans", []):
            walk(child)

    walk(raw[0]["Plan"])
    return ", ".join(dict.fromkeys(found))


def run_all(conn, queries):
    return {name: (time_query(conn, sql), scan_summary(conn, sql))
            for name, _, sql in queries}


def sizes(conn):
    return conn.exec_driver_sql("""
        SELECT pg_size_pretty(pg_table_size('perf.clean_transactions')),
               pg_size_pretty(pg_total_relation_size('perf.clean_transactions')),
               pg_size_pretty(pg_total_relation_size('perf.alerts'))""").fetchone()


def md_table(df):
    cols = list(df.columns)
    lines = ["| " + " | ".join(cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for row in df.itertuples(index=False):
        cells = [f"{v:,.1f}" if isinstance(v, float) else str(v) for v in row]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--rows", type=int, default=1_000_000)
    p.add_argument("--drop", action="store_true", help="delete the perf schema and exit")
    args = p.parse_args()

    engine = create_engine(DB_URL)
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        if args.drop:
            conn.exec_driver_sql("DROP SCHEMA IF EXISTS perf CASCADE")
            print("perf schema dropped.")
            return

        load(conn, args.rows)
        conn.exec_driver_sql("SET search_path TO perf")   # queries now hit perf tables
        n_txn = conn.exec_driver_sql("SELECT count(*) FROM clean_transactions").scalar()
        n_alert = conn.exec_driver_sql("SELECT count(*) FROM alerts").scalar()
        queries = make_queries(args.rows)

        print("Timing queries BEFORE indexes (primary keys only)...")
        size_before = sizes(conn)
        before = run_all(conn, queries)

        print("Creating indexes...")
        t0 = time.perf_counter()
        conn.exec_driver_sql(Path("sql/02_indexes.sql").read_text(encoding="utf-8"))
        conn.exec_driver_sql("ANALYZE clean_transactions")
        conn.exec_driver_sql("ANALYZE alerts")
        index_seconds = time.perf_counter() - t0
        size_after = sizes(conn)

        print("Timing queries AFTER indexes...")
        after = run_all(conn, queries)

    rows = []
    for name, dash, _ in queries:
        b, a = before[name][0], after[name][0]
        rows.append({"query": name, "dashboard": dash, "before_ms": b, "after_ms": a,
                     "speedup_x": b / a if a else 0.0,
                     "plan_before": before[name][1], "plan_after": after[name][1]})
    df = pd.DataFrame(rows)
    dash_df = df[df["dashboard"]]
    tb, ta = dash_df["before_ms"].sum(), dash_df["after_ms"].sum()

    size_df = pd.DataFrame({
        "": ["transactions table (data only)", "transactions incl. indexes", "alerts incl. indexes"],
        "before": [size_before[0], size_before[1], size_before[2]],
        "after": [size_before[0], size_after[1], size_after[2]],
    })
    text = "\n\n".join([
        "# Query performance: before vs after indexes",
        f"_Generated {pd.Timestamp.now():%Y-%m-%d %H:%M}. Local PostgreSQL, {n_txn:,} synthetic "
        f"transactions and {n_alert:,} alerts spread over 30 days; queries filter the last 7 days. "
        "Median of 7 runs after 1 warm-up. 'Before' = primary keys only. Rows were generated "
        "directly in SQL for load testing; this measures query speed, not detection quality._",
        "## Results", md_table(df.drop(columns="dashboard")),
        f"**One full dashboard refresh (the 5 dashboard queries): {tb:,.0f} ms -> {ta:,.0f} ms "
        f"({tb / ta:.1f}x faster).**" if ta else "",
        f"Index build time: {index_seconds:.1f}s.",
        "## Storage cost of the indexes", md_table(size_df),
    ])
    REPORTS.mkdir(exist_ok=True)
    (REPORTS / "benchmark.md").write_text(text, encoding="utf-8")
    print("\n" + text)
    print("\nSaved reports/benchmark.md. Delete the test data with: python -m src.benchmark --drop")


if __name__ == "__main__":
    main()