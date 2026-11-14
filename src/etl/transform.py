import json

import numpy as np
import pandas as pd

IST = "Asia/Kolkata"
UPI_RE = r"^[a-z0-9][a-z0-9._-]+@[a-z]{2,}$"
AWARE_FORMATS = ["%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ"]
NAIVE_FORMATS = ["%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M", "%d-%b-%Y %I:%M %p"]
FUTURE_TOLERANCE = pd.Timedelta(minutes=5)
MAX_AMOUNT = 10_000_000


def parse_amount(s):
    cleaned = s.str.replace(r"rs\.?|inr|₹|,", "", case=False, regex=True).str.strip()
    return pd.to_numeric(cleaned, errors="coerce")


def parse_timestamps(s):
    """Try each known format. Timestamps without a zone are treated as IST.
    Explicit formats avoid the dd/mm vs mm/dd trap. Result is always UTC."""
    result = pd.to_datetime(pd.Series(pd.NaT, index=s.index), utc=True)
    for fmt in AWARE_FORMATS:
        result = result.fillna(pd.to_datetime(s, format=fmt, errors="coerce", utc=True))
    for fmt in NAIVE_FORMATS:
        parsed = pd.to_datetime(s, format=fmt, errors="coerce")
        parsed = parsed.dt.tz_localize(IST, ambiguous="NaT", nonexistent="NaT").dt.tz_convert("UTC")
        result = result.fillna(parsed)
    return result


def normalize_status(s):
    up = s.str.strip().str.upper()
    out = pd.Series(None, index=s.index, dtype="object")
    out[up.str.startswith("SUCC", na=False)] = "SUCCESS"
    out[up.str.startswith("FAIL", na=False)] = "FAILED"
    return out


def valid_upi(s):
    return s.str.strip().str.lower().str.match(UPI_RE, na=False)


def clean(raw):
    """Returns (clean_df, rejected_df, stats). raw must be all-text columns."""
    rows_in = len(raw)
    df = raw.copy()
    for c in df.columns:
        df[c] = df[c].str.strip().replace("", pd.NA)

    # 1. duplicates (retries): keep the first occurrence of each txn_id
    dup = df["txn_id"].notna() & df.duplicated("txn_id", keep="first")
    duplicates_removed = int(dup.sum())
    df = df[~dup].copy()

    # 2. parse and normalize
    df["amount_num"] = parse_amount(df["amount"])
    df["txn_ts"] = parse_timestamps(df["timestamp"])
    df["status_norm"] = normalize_status(df["status"])
    df["sender_norm"] = df["sender_upi"].str.lower()
    df["receiver_norm"] = df["receiver_upi"].str.lower()

    # 3. validation rules
    now = pd.Timestamp.now(tz="UTC")
    checks = {
        "missing_txn_id": df["txn_id"].isna(),
        "unparseable_amount": df["amount_num"].isna(),
        "non_positive_amount": df["amount_num"].le(0),
        "amount_out_of_range": df["amount_num"].gt(MAX_AMOUNT),
        "unparseable_timestamp": df["txn_ts"].isna(),
        "future_timestamp": df["txn_ts"] > now + FUTURE_TOLERANCE,
        "invalid_status": df["status_norm"].isna(),
        "invalid_sender_upi": ~valid_upi(df["sender_upi"]),
        "invalid_receiver_upi": ~valid_upi(df["receiver_upi"]),
    }
    names = list(checks)
    flags = np.column_stack([m.fillna(False).to_numpy(dtype=bool) for m in checks.values()])
    reasons = pd.Series([";".join(n for n, f in zip(names, row) if f) for row in flags],
                        index=df.index)
    bad = reasons != ""

    good = df.loc[~bad]
    clean_df = pd.DataFrame({
        "txn_id": good["txn_id"],
        "txn_ts": good["txn_ts"],
        "sender_upi": good["sender_norm"],
        "receiver_upi": good["receiver_norm"],
        "amount": good["amount_num"].round(2),
        "status": good["status_norm"],
        "device_id": good["device_id"],
        "location": good["location"],
    }).reset_index(drop=True)

    # rejected rows keep the row exactly as it was received
    raw_bad = raw.loc[df.index[bad]]
    records = raw_bad.astype(object).where(raw_bad.notna(), None).to_dict("records")
    rejected_df = pd.DataFrame({
        "raw_data": [json.dumps(r, ensure_ascii=False) for r in records],
        "reject_reason": reasons[bad].tolist(),
    })

    stats = {
        "rows_in": int(rows_in),
        "duplicates_removed": duplicates_removed,
        "rows_cleaned": int(len(clean_df)),
        "rows_rejected": int(len(rejected_df)),
    }
    return clean_df, rejected_df, stats