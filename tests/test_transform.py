import pandas as pd
from src.etl import transform as t


def S(values):
    return pd.Series(values, dtype="object")


def test_parse_amount_formats():
    out = t.parse_amount(S(["₹1,299.00", "Rs. 1,299.00", "1299", "1299.00", "abc"]))
    assert out.iloc[:4].tolist() == [1299.0] * 4
    assert pd.isna(out.iloc[4])


def test_all_timestamp_formats_give_same_instant():
    vals = ["2026-11-05 18:37:00", "05/11/2026 18:37", "05-Nov-2026 06:37 PM",
            "2026-11-05T18:37:00+05:30", "2026-11-05T13:07:00Z"]
    out = t.parse_timestamps(S(vals))
    assert out.notna().all()
    assert (out == pd.Timestamp("2026-11-05 13:07:00", tz="UTC")).all()


def test_status_normalization():
    out = t.normalize_status(S(["SUCCESS", "success", "Succ", "Success ", "FAIL", "failed", "??"]))
    assert out.iloc[:6].tolist() == ["SUCCESS"] * 4 + ["FAILED"] * 2
    assert pd.isna(out.iloc[6])


def test_upi_validation():
    out = t.valid_upi(S(["priya12@oksbi", "name@@ybl", "noatsign", "a@b"]))
    assert out.tolist() == [True, False, False, False]


def test_clean_end_to_end():
    raw = pd.DataFrame({
        "txn_id": ["T1", "T1", "T2", "T3", "T4", "T5"],
        "timestamp": ["2026-11-05 10:00:00"] * 4 + ["2099-01-01 10:00:00", "2026-11-05 10:00:00"],
        "sender_upi": ["a1@oksbi", "a1@oksbi", "a1@oksbi", "bad@@ybl", "a1@oksbi", "a1@oksbi"],
        "receiver_upi": ["b2@ybl"] * 6,
        "amount": ["₹1,299.00", "₹1,299.00", "Rs. 50", "100", "100", "-5"],
        "status": ["Succ", "Succ", "FAILED", "success", "success", "success"],
        "device_id": [None] * 6,
        "location": [None] * 6,
    })
    clean, rejected, stats = t.clean(raw)
    assert stats["rows_in"] == 6
    assert stats["duplicates_removed"] == 1
    assert len(clean) == 2        # T1, T2
    assert len(rejected) == 3     # T3 bad UPI, T4 future date, T5 negative amount