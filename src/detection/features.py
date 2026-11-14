import numpy as np
import pandas as pd
from sqlalchemy import text

IST = "Asia/Kolkata"
LIMIT_AMOUNTS = [9999, 49999, 99999]  # just below common UPI limits


def load_transactions(engine):
    df = pd.read_sql(
        "SELECT txn_id, txn_ts, sender_upi, receiver_upi, amount, status, "
        "device_id, location FROM clean_transactions", engine)
    df["txn_ts"] = pd.to_datetime(df["txn_ts"], utc=True)
    df["amount"] = df["amount"].astype(float)  # NUMERIC arrives as Decimal
    df["ts_s"] = (df["txn_ts"] - pd.Timestamp("1970-01-01", tz="UTC")).dt.total_seconds()
    return df.sort_values(["sender_upi", "txn_ts"]).reset_index(drop=True)


def window_sum(df, key, values, back_s, fwd_s):
    """For every row: the sum of `values` over rows with the same `key` whose time is
    within [t - back_s, t + fwd_s] (the row itself included). Fully vectorised."""
    codes, _ = pd.factorize(df[key])
    ts = df["ts_s"].to_numpy()
    span = ts.max() - ts.min() + 1_000_000      # gap between groups > any window
    comp = codes * span + (ts - ts.min())
    order = np.argsort(comp, kind="stable")
    comp_s = comp[order]
    vals = np.asarray(values, dtype=float)[order]
    csum = np.concatenate([[0.0], np.cumsum(vals)])
    lo = np.searchsorted(comp_s, comp_s - back_s, side="left")
    hi = np.searchsorted(comp_s, comp_s + fwd_s, side="right")
    out = np.empty(len(df))
    out[order] = csum[hi] - csum[lo]
    return out


def usual_value(df, col):
    """Most common non-null value of `col` per sender."""
    return (df.dropna(subset=[col]).groupby(["sender_upi", col]).size()
              .reset_index(name="n")
              .sort_values(["sender_upi", "n"], ascending=[True, False])
              .drop_duplicates("sender_upi")
              .set_index("sender_upi")[col])


def build_features(df):
    f = df.copy()
    ones = np.ones(len(f))
    f["hour_ist"] = f["txn_ts"].dt.tz_convert(IST).dt.hour

    # who is this user normally?
    f["median_amount"] = f.groupby("sender_upi")["amount"].transform("median")
    f["txn_count_user"] = f.groupby("sender_upi")["txn_id"].transform("count")
    f["amount_ratio"] = f["amount"] / f["median_amount"]
    f["is_first_pair"] = ~f.duplicated(["sender_upi", "receiver_upi"], keep="first")
    f["usual_device"] = f["sender_upi"].map(usual_value(f, "device_id"))
    f["usual_location"] = f["sender_upi"].map(usual_value(f, "location"))
    f["device_changed"] = (f["device_id"].notna() & f["usual_device"].notna()
                           & (f["device_id"] != f["usual_device"]))
    f["location_changed"] = (f["location"].notna() & f["usual_location"].notna()
                             & (f["location"] != f["usual_location"]))
    f["secs_since_prev"] = f.groupby("sender_upi")["ts_s"].diff().fillna(30 * 86400)

    # velocity: transactions by the same sender in a 5 minute window
    f["tx_5m_back"] = window_sum(f, "sender_upi", ones, 300, 0)
    f["tx_5m_fwd"] = window_sum(f, "sender_upi", ones, 0, 300)

    # failed attempts followed by a success
    failed = (f["status"] == "FAILED").astype(float).to_numpy()
    success = (f["status"] == "SUCCESS").astype(float).to_numpy()
    f["failed_10m_around"] = window_sum(f, "sender_upi", failed, 600, 600)
    f["failed_10m_back"] = window_sum(f, "sender_upi", failed, 600, 0)
    f["success_10m_fwd"] = window_sum(f, "sender_upi", success, 0, 600)

    # repeated just-below-limit amounts
    f["near_limit"] = f["amount"].isin(LIMIT_AMOUNTS)
    f["near_limit_1h"] = window_sum(f, "sender_upi",
                                    f["near_limit"].astype(float).to_numpy(), 3600, 3600)

    # mule fan-in: distinct senders paying one account within an hour.
    # Two overlapping hourly bucketings (offset 30 min) so a burst is never split.
    for tag, shift in (("a", 0), ("b", 1800)):
        f["_bucket"] = ((f["ts_s"] + shift) // 3600).astype("int64")
        f[f"fan_in_{tag}"] = (f.groupby(["receiver_upi", "_bucket"])["sender_upi"]
                               .transform("nunique"))
    f["fan_in_senders_1h"] = f[["fan_in_a", "fan_in_b"]].max(axis=1)
    f = f.drop(columns=["_bucket", "fan_in_a", "fan_in_b"])

    # mule outflow: the fan-in account sends money out soon after the burst
    fan = f[f["fan_in_senders_1h"] >= 8]
    burst = fan.groupby("receiver_upi")["ts_s"].agg(burst_start="min", burst_end="max")
    f["burst_start"] = f["sender_upi"].map(burst["burst_start"])
    f["burst_end"] = f["sender_upi"].map(burst["burst_end"])
    f["mule_outflow_flag"] = (f["burst_end"].notna() & (f["ts_s"] > f["burst_start"])
                              & (f["ts_s"] <= f["burst_end"] + 3600))
    return f


def build_user_features(f):
    g = f.groupby("sender_upi")
    uf = pd.DataFrame({
        "avg_amount": g["amount"].mean().round(2),
        "median_amount": g["amount"].median().round(2),
        "txn_count": g.size(),
    }).reset_index().rename(columns={"sender_upi": "upi_id"})
    uf["usual_device"] = uf["upi_id"].map(usual_value(f, "device_id"))
    uf["usual_location"] = uf["upi_id"].map(usual_value(f, "location"))
    return uf


def save_user_features(conn, uf):
    uf.to_sql("stg_user_features", conn, if_exists="replace", index=False,
              chunksize=2000, method="multi")
    conn.execute(text("""
        INSERT INTO user_features
            (upi_id, avg_amount, median_amount, txn_count, usual_device, usual_location, updated_at)
        SELECT upi_id, CAST(avg_amount AS NUMERIC(12,2)), CAST(median_amount AS NUMERIC(12,2)),
               txn_count, usual_device, usual_location, now()
        FROM stg_user_features
        ON CONFLICT (upi_id) DO UPDATE SET
            avg_amount = EXCLUDED.avg_amount, median_amount = EXCLUDED.median_amount,
            txn_count = EXCLUDED.txn_count, usual_device = EXCLUDED.usual_device,
            usual_location = EXCLUDED.usual_location, updated_at = now()
    """))
    conn.execute(text("DROP TABLE stg_user_features"))