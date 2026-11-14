import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest


def anomaly_scores(f):
    """IsolationForest on behavioural features. Returns 0-100 (percentile rank:
    100 = most unusual transaction in the data)."""
    X = pd.DataFrame({
        "log_amount": np.log1p(f["amount"]),
        "amount_ratio": f["amount_ratio"].clip(upper=50),
        "hour_ist": f["hour_ist"],
        "tx_5m": f["tx_5m_back"],
        "first_pair": f["is_first_pair"].astype(int),
        "device_changed": f["device_changed"].astype(int),
        "location_changed": f["location_changed"].astype(int),
        "failed": (f["status"] == "FAILED").astype(int),
        "fan_in": f["fan_in_senders_1h"],
        "log_gap": np.log1p(f["secs_since_prev"]),
    })
    model = IsolationForest(n_estimators=200, contamination=0.02,
                            random_state=42, n_jobs=-1)
    model.fit(X)
    raw = -model.score_samples(X)               # higher = more unusual
    return pd.Series(raw, index=f.index).rank(pct=True).to_numpy() * 100