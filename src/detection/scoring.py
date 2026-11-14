import time

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

from src.config import DB_URL
from src.detection.anomaly import anomaly_scores
from src.detection.features import (build_features, build_user_features,
                                    load_transactions, save_user_features)
from src.detection.rules import apply_rules

RULE_W, ANOMALY_W = 0.7, 0.3
ANOMALY_ONLY_PERCENTILE = 99.5   # alert on anomaly alone only for the top 0.5%


def score_all(f):
    rule_score, triggered = apply_rules(f)
    anomaly = anomaly_scores(f)
    risk = RULE_W * rule_score + ANOMALY_W * anomaly
    out = pd.DataFrame({
        "txn_id": f["txn_id"].to_numpy(),
        "risk_score": np.round(risk, 2),
        "rule_score": np.round(rule_score, 2),
        "anomaly_score": np.round(anomaly, 2),
        "rules_triggered": triggered,
    })
    keep = (rule_score > 0) | (anomaly >= ANOMALY_ONLY_PERCENTILE)
    return out[keep].sort_values("risk_score", ascending=False).reset_index(drop=True)


def save_alerts(conn, alerts):
    # re-scoring is repeatable: open alerts are rebuilt, reviewed ones are kept
    conn.execute(text("DELETE FROM alerts WHERE status = 'open'"))
    if alerts.empty:
        return
    params = [{"txn_id": r.txn_id, "risk": float(r.risk_score), "rule": float(r.rule_score),
               "anom": float(r.anomaly_score), "rules": list(r.rules_triggered)}
              for r in alerts.itertuples()]
    conn.execute(text("""
        INSERT INTO alerts (txn_id, risk_score, rule_score, anomaly_score, rules_triggered)
        VALUES (:txn_id, :risk, :rule, :anom, :rules)
        ON CONFLICT (txn_id) DO UPDATE SET
            risk_score = EXCLUDED.risk_score, rule_score = EXCLUDED.rule_score,
            anomaly_score = EXCLUDED.anomaly_score,
            rules_triggered = EXCLUDED.rules_triggered
    """), params)


def main():
    t0 = time.perf_counter()
    engine = create_engine(DB_URL)
    df = load_transactions(engine)
    if df.empty:
        print("clean_transactions is empty. Run python -m src.pipeline first.")
        return
    f = build_features(df)
    alerts = score_all(f)
    with engine.begin() as conn:
        save_user_features(conn, build_user_features(f))
        save_alerts(conn, alerts)

    print(f"Scored {len(f)} transactions -> {len(alerts)} alerts "
          f"in {time.perf_counter() - t0:.1f}s")
    print("Alerts per rule:")
    print(pd.Series([r for row in alerts.rules_triggered for r in row]).value_counts())


if __name__ == "__main__":
    main()