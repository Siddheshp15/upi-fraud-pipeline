"""Evaluate detection against the hidden ground truth.
Writes reports/metrics.md, reports/missed_fraud.csv, reports/top_false_positives.csv."""
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, roc_auc_score
from sqlalchemy import create_engine

from src.config import DB_URL
from src.detection.anomaly import anomaly_scores
from src.detection.features import build_features, load_transactions
from src.detection.rules import apply_rules
from src.detection.scoring import ANOMALY_W, RULE_W

GT_FILE = Path("data/ground_truth.csv")
REPORTS = Path("reports")


def summarize(tp, n_alerts, positives, negatives):
    fp = n_alerts - tp
    precision = tp / n_alerts if n_alerts else 0.0
    recall = tp / positives if positives else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    fpr = fp / negatives if negatives else 0.0
    return {"tp": tp, "fp": fp, "fn": positives - tp,
            "precision": precision, "recall": recall, "f1": f1, "fpr": fpr}


def md_table(df, floatfmt="{:.2f}"):
    if df.empty:
        return "(none)"
    cols = list(df.columns)
    lines = ["| " + " | ".join(map(str, cols)) + " |",
             "|" + "|".join("---" for _ in cols) + "|"]
    for row in df.itertuples(index=False):
        cells = [floatfmt.format(v) if isinstance(v, (float, np.floating)) else str(v)
                 for v in row]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main():
    REPORTS.mkdir(exist_ok=True)
    engine = create_engine(DB_URL)

    # ---- label every clean transaction from the answer key
    clean_ids = pd.read_sql("SELECT txn_id FROM clean_transactions", engine)
    gt = pd.read_csv(GT_FILE).drop_duplicates("txn_id")
    data = clean_ids.merge(gt, on="txn_id", how="left")
    unlabeled = int(data["is_fraud"].isna().sum())
    if unlabeled:
        print(f"WARNING: {unlabeled} clean transactions have no label (treated as normal).")
    data["is_fraud"] = data["is_fraud"].fillna(0).astype(int)
    data["fraud_type"] = data["fraud_type"].fillna("none")
    total, P = len(data), int(data["is_fraud"].sum())
    N = total - P

    # ---- alerts exactly as an analyst sees them
    alerts = pd.read_sql(
        "SELECT txn_id, risk_score, rule_score, anomaly_score, rules_triggered FROM alerts",
        engine)
    for c in ("risk_score", "rule_score", "anomaly_score"):
        alerts[c] = alerts[c].astype(float)
    m = (alerts.merge(data, on="txn_id", how="left")
               .sort_values("risk_score", ascending=False).reset_index(drop=True))

    s = summarize(int(m["is_fraud"].sum()), len(m), P, N)
    headline = pd.DataFrame({"metric": [
        "Transactions evaluated", "Real fraud transactions", "Alerts raised",
        "True positives", "False positives", "Missed fraud",
        "Precision %", "Recall %", "F1 %", "False positive rate %"],
        "value": [f"{total:,}", f"{P:,}", f"{len(m):,}", s["tp"], s["fp"], s["fn"],
                  f"{100 * s['precision']:.1f}", f"{100 * s['recall']:.1f}",
                  f"{100 * s['f1']:.1f}", f"{100 * s['fpr']:.3f}"]})

    pk = []
    for k in (50, 100, 200):
        if len(m) >= k:
            top = m.head(k)
            pk.append({"K": k, "fraud_in_top_K": int(top["is_fraud"].sum()),
                       "precision_%": 100 * top["is_fraud"].mean()})
    pk = pd.DataFrame(pk)

    # ---- recall per fraud pattern
    fr = data[data["is_fraud"] == 1].copy()
    fr["caught"] = fr["txn_id"].isin(alerts["txn_id"])
    per_type = (fr.groupby("fraud_type")
                  .agg(fraud_txns=("txn_id", "count"), caught=("caught", "sum"))
                  .reset_index())
    per_type["recall_%"] = 100 * per_type["caught"] / per_type["fraud_txns"]
    per_type = per_type.sort_values("recall_%").reset_index(drop=True)

    # ---- precision per rule (an alert with 2 rules counts under both)
    ex = m.explode("rules_triggered")
    ex["rules_triggered"] = ex["rules_triggered"].fillna("(anomaly only)")
    per_rule = (ex.groupby("rules_triggered")
                  .agg(alerts=("txn_id", "count"), true_fraud=("is_fraud", "sum"))
                  .reset_index().rename(columns={"rules_triggered": "rule"}))
    per_rule["precision_%"] = 100 * per_rule["true_fraud"] / per_rule["alerts"]
    per_rule = per_rule.sort_values("alerts", ascending=False).reset_index(drop=True)

    # ---- rules vs IsolationForest vs combined (re-scored on all transactions)
    f = build_features(load_transactions(engine))
    y = f["txn_id"].map(data.set_index("txn_id")["is_fraud"]).fillna(0).astype(int).to_numpy()
    R = int(y.sum())
    rule_score, _ = apply_rules(f)
    anomaly = anomaly_scores(f)
    combined = RULE_W * rule_score + ANOMALY_W * anomaly
    scores = {"Rules only": rule_score, "IsolationForest only": anomaly,
              "Rules + IsolationForest": combined}
    rows = []
    for name, sc in scores.items():
        ys = y[np.argsort(-sc, kind="stable")]   # ties in rule score broken by row order
        rows.append({"approach": name,
                     "precision@100_%": 100 * ys[:100].mean(),
                     f"precision@{R}_%": 100 * ys[:R].mean(),
                     "avg_precision": average_precision_score(y, sc),
                     "roc_auc": roc_auc_score(y, sc)})
    compare = pd.DataFrame(rows)

    # ---- threshold sweep on the combined risk score
    sweep = []
    for t in (10, 20, 30, 40, 50, 60, 70):
        flag = combined >= t
        n = int(flag.sum())
        tp_t = int(y[flag].sum())
        sweep.append({"risk_threshold": t, "alerts": n,
                      "precision_%": 100 * tp_t / n if n else 0.0,
                      "recall_%": 100 * tp_t / R if R else 0.0,
                      "false_positive_rate_%": 100 * (n - tp_t) / (len(y) - R)})
    sweep = pd.DataFrame(sweep)

    # ---- data quality and throughput from the run log
    runs = pd.read_sql(
        "SELECT run_id, source_file, rows_in, rows_cleaned, rows_rejected, "
        "duplicates_removed, duration_seconds FROM pipeline_runs "
        "WHERE status = 'success' ORDER BY run_id", engine)
    runs["duration_seconds"] = runs["duration_seconds"].astype(float)
    den = runs["rows_in"].clip(lower=1)
    runs["cleaned_%"] = 100 * runs["rows_cleaned"] / den
    runs["rejected_%"] = 100 * runs["rows_rejected"] / den
    runs["duplicates_%"] = 100 * runs["duplicates_removed"] / den
    runs["rows_per_sec"] = runs["rows_in"] / runs["duration_seconds"].clip(lower=0.01)
    rej = pd.read_sql("SELECT reject_reason, count(*) AS rows FROM rejected_rows "
                      "GROUP BY 1 ORDER BY 2 DESC", engine)

    # ---- debugging files
    details = f[["txn_id", "txn_ts", "sender_upi", "receiver_upi", "amount",
                 "status", "hour_ist"]]
    missed = fr[~fr["caught"]][["txn_id", "fraud_type"]].merge(details, on="txn_id")
    missed.to_csv(REPORTS / "missed_fraud.csv", index=False)
    fps = m[m["is_fraud"] == 0].head(50).merge(details, on="txn_id")
    fps["rules_triggered"] = fps["rules_triggered"].map(";".join)
    fps.to_csv(REPORTS / "top_false_positives.csv", index=False)

    parts = [
        "# Detection evaluation",
        f"_Generated {pd.Timestamp.now():%Y-%m-%d %H:%M}. Ground truth = synthetic fraud injected "
        "by my own generator, so results show how well the system finds those patterns, "
        "not how it would perform on real UPI fraud._",
        "## Headline (alerts in the alerts table)", md_table(headline),
        "## Precision at K (ranking quality)", md_table(pk),
        "## Recall per fraud pattern", md_table(per_type),
        "## Precision per rule", md_table(per_rule),
        "## Rules vs IsolationForest vs combined", md_table(compare),
        "## Risk threshold trade-off (combined score)", md_table(sweep),
        "## Pipeline runs: data quality and throughput", md_table(runs),
        "### Rejection reasons", md_table(rej),
    ]
    text = "\n\n".join(parts)
    (REPORTS / "metrics.md").write_text(text, encoding="utf-8")
    print(text)
    print("\nSaved: reports/metrics.md, reports/missed_fraud.csv, reports/top_false_positives.csv")


if __name__ == "__main__":
    main()