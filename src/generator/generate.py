"""Synthetic UPI transaction generator: messy output + hidden fraud labels."""
import argparse
import random
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from faker import Faker

DATA_DIR = Path("data")
INCOMING = DATA_DIR / "incoming"
USERS_FILE = DATA_DIR / "users.csv"
LABELS_FILE = DATA_DIR / "ground_truth.csv"

BANKS = ["okhdfcbank", "oksbi", "okicici", "ybl", "paytm", "axl", "ibl"]
CITIES = ["Mumbai", "Delhi", "Bengaluru", "Chennai", "Kolkata",
          "Hyderabad", "Pune", "Jaipur", "Guwahati", "Kochi"]

# Hour-of-day weights: busy daytime, quiet nights
HOUR_W = np.array([1, 1, 1, 1, 1, 2, 3, 5, 7, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 7, 6, 4, 2],
                  dtype=float)
HOUR_W /= HOUR_W.sum()

fake = Faker("en_IN")


# ---------------------------------------------------------------- users
def generate_users(n=2000):
    rows, used = [], set()
    while len(rows) < n:
        name = fake.first_name().lower()
        upi = f"{name}{random.randint(1, 9999)}@{random.choice(BANKS)}"
        if upi in used:
            continue
        used.add(upi)
        rows.append({
            "upi_id": upi,
            "device_id": "DEV" + uuid.uuid4().hex[:8].upper(),
            "city": random.choice(CITIES),
            "avg_amount": round(float(np.random.lognormal(np.log(800), 0.6)), 2),
        })
    ids = [r["upi_id"] for r in rows]
    for r in rows:  # each user has a small set of regular payees
        r["payees"] = ";".join([p for p in random.sample(ids, 9) if p != r["upi_id"]][:8])
    return pd.DataFrame(rows)


def load_or_create_users():
    if not USERS_FILE.exists():
        DATA_DIR.mkdir(exist_ok=True)
        generate_users().to_csv(USERS_FILE, index=False, encoding="utf-8")
    users = pd.read_csv(USERS_FILE).to_dict("records")
    for u in users:
        u["payees"] = u["payees"].split(";")
    return users


# ---------------------------------------------------------------- helpers
def random_ts(start, end):
    span = end - start
    if span < timedelta(days=1):   # live mode: uniform inside the short window
        return start + timedelta(seconds=random.randint(0, max(int(span.total_seconds()), 0)))
    days = max((end.date() - start.date()).days, 0)
    day = start.date() + timedelta(days=random.randint(0, days))
    hour = int(np.random.choice(24, p=HOUR_W))
    ts = datetime(day.year, day.month, day.day, hour,
                  random.randint(0, 59), random.randint(0, 59))
    return min(ts, end)


def base_time(start, end):
    margin = min(timedelta(hours=1), (end - start) / 2)
    return random_ts(start, end - margin)


def txn(sender, receiver, amount, ts, status="SUCCESS", device=None, city=None, fraud=None):
    return {
        "txn_id": "T" + uuid.uuid4().hex[:12].upper(),
        "timestamp": ts,
        "sender_upi": sender["upi_id"],
        "receiver_upi": receiver,
        "amount": round(float(amount), 2),
        "status": status,
        "device_id": device or sender["device_id"],
        "location": city or sender["city"],
        "fraud_type": fraud,
    }


def normal_txn(u, ids, start, end):
    receiver = random.choice(u["payees"]) if random.random() < 0.9 else random.choice(ids)
    if receiver == u["upi_id"]:
        receiver = random.choice(u["payees"])
    amount = np.clip(np.random.lognormal(np.log(u["avg_amount"]), 0.5), 10, u["avg_amount"] * 4)
    status = "SUCCESS" if random.random() < 0.97 else "FAILED"
    return txn(u, receiver, amount, random_ts(start, end), status=status)


# ---------------------------------------------------------------- fraud injectors
def inject_velocity(users, ids, start, end):
    u, t0 = random.choice(users), base_time(start, end)
    return [txn(u, random.choice(ids), random.uniform(200, 3000),
                t0 + timedelta(seconds=random.randint(0, 240)), fraud="velocity")
            for _ in range(random.randint(10, 15))]


def inject_amount_spike(users, ids, start, end):
    u = random.choice(users)
    return [txn(u, random.choice(u["payees"]), u["avg_amount"] * random.uniform(6, 12),
                random_ts(start, end), fraud="amount_spike")]


def inject_new_payee_high(users, ids, start, end):
    u = random.choice(users)
    receiver = random.choice([i for i in ids if i not in u["payees"] and i != u["upi_id"]])
    return [txn(u, receiver, random.uniform(20000, 80000),
                random_ts(start, end), fraud="new_payee_high")]


def inject_mule(users, ids, start, end):
    mule, t0 = random.choice(users), base_time(start, end)
    rows, total = [], 0
    for s in random.sample([u for u in users if u is not mule], random.randint(8, 15)):
        amt = random.uniform(2000, 9000)
        total += amt
        rows.append(txn(s, mule["upi_id"], amt,
                        t0 + timedelta(minutes=random.randint(0, 30)), fraud="mule"))
    rows.append(txn(mule, random.choice(ids), total * 0.9,
                    t0 + timedelta(minutes=random.randint(35, 45)), fraud="mule"))
    return rows


def inject_odd_hours(users, ids, start, end):
    u = random.choice(users)
    day = random_ts(start, end).date()
    ts = datetime(day.year, day.month, day.day, random.randint(1, 3), random.randint(0, 59))
    ts = min(ts, end)
    return [txn(u, random.choice(ids), random.uniform(15000, 60000), ts, fraud="odd_hours")]


def inject_failed_then_success(users, ids, start, end):
    u, t0 = random.choice(users), base_time(start, end)
    r, amt = random.choice(ids), random.uniform(3000, 25000)
    rows = [txn(u, r, amt, t0 + timedelta(seconds=30 * i), status="FAILED",
                fraud="failed_then_success") for i in range(random.randint(3, 5))]
    rows.append(txn(u, r, amt, t0 + timedelta(seconds=30 * len(rows)),
                    fraud="failed_then_success"))
    return rows


def inject_device_location_change(users, ids, start, end):
    u, t0 = random.choice(users), base_time(start, end)
    normal = txn(u, random.choice(u["payees"]), u["avg_amount"], t0)  # not fraud
    bad = txn(u, random.choice(ids), random.uniform(10000, 50000),
              t0 + timedelta(minutes=random.randint(2, 10)),
              device="DEV" + uuid.uuid4().hex[:8].upper(),
              city=random.choice([c for c in CITIES if c != u["city"]]),
              fraud="device_location_change")
    return [normal, bad]


def inject_round_amount(users, ids, start, end):
    u, t0 = random.choice(users), base_time(start, end)
    amt = random.choice([9999, 49999])
    return [txn(u, random.choice(ids), amt, t0 + timedelta(minutes=10 * i),
                fraud="round_amount") for i in range(random.randint(3, 5))]


INJECTORS = [inject_velocity, inject_amount_spike, inject_new_payee_high, inject_mule,
             inject_odd_hours, inject_failed_then_success,
             inject_device_location_change, inject_round_amount]


# ---------------------------------------------------------------- batch + mess
def generate_batch(users, n, start, end, fraud_rate):
    ids = [u["upi_id"] for u in users]
    rows = [normal_txn(random.choice(users), ids, start, end) for _ in range(n)]
    events = max(1, int(n * fraud_rate / len(INJECTORS) / 5))  # ~5 txns per fraud event
    for inj in INJECTORS:
        for _ in range(events):
            rows += inj(users, ids, start, end)
    return pd.DataFrame(rows)


def fmt_amount(a):
    k = random.choice(["plain", "rupee", "int", "rs"])
    if k == "plain":
        return f"{a:.2f}"
    if k == "rupee":
        return f"₹{a:,.2f}"
    if k == "int":
        return f"{a:.0f}"
    return f"Rs. {a:,.2f}"


def fmt_ts(ts):
    k = random.randint(0, 4)
    if k == 0:
        return ts.strftime("%Y-%m-%d %H:%M:%S")
    if k == 1:
        return ts.strftime("%d/%m/%Y %H:%M")
    if k == 2:
        return ts.strftime("%d-%b-%Y %I:%M %p")
    if k == 3:
        return ts.strftime("%Y-%m-%dT%H:%M:%S+05:30")
    return (ts - timedelta(hours=5, minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")


STATUS_VARIANTS = {"SUCCESS": ["SUCCESS", "success", "Succ", "Success "],
                   "FAILED": ["FAILED", "failed", "FAIL", "Failed"]}


def mess_up(df):
    df = df.copy()
    labels = df[["txn_id", "fraud_type"]].copy()
    labels["is_fraud"] = labels["fraud_type"].notna().astype(int)
    labels["fraud_type"] = labels["fraud_type"].fillna("none")
    is_fraud = df["fraud_type"].notna()
    df = df.drop(columns="fraud_type")

    # invalid/missing values go on NON-fraud rows only, so every fraud row
    # survives cleaning and recall stays measurable
    idx = df.index[~is_fraud]

    def pick(frac):
        return np.random.choice(idx, size=int(len(idx) * frac), replace=False)

    i = pick(0.005)
    df.loc[i, "amount"] = -df.loc[i, "amount"]                       # negative amounts
    i = pick(0.005)
    df.loc[i, "timestamp"] = df.loc[i, "timestamp"] + pd.Timedelta(days=random.randint(1, 30))  # future dates
    df.loc[pick(0.03), "device_id"] = None                            # missing device
    df.loc[pick(0.03), "location"] = None                             # missing location
    i = pick(0.01)
    df.loc[i, "sender_upi"] = df.loc[i, "sender_upi"].str.replace("@", "@@")   # name@@bank
    i = pick(0.01)
    df.loc[i, "receiver_upi"] = df.loc[i, "receiver_upi"].str.replace("@", "")  # no @ at all

    # messy formats on every row
    df["amount"] = df["amount"].map(fmt_amount)
    df["timestamp"] = df["timestamp"].map(fmt_ts)
    df["status"] = df["status"].map(lambda s: random.choice(STATUS_VARIANTS[s]))

    # duplicates (retries), then shuffle so the file is out of order
    df = pd.concat([df, df.sample(frac=0.02)])
    df = df.sample(frac=1).reset_index(drop=True)
    return df, labels


# ---------------------------------------------------------------- main
def run(n=20000, days=30, fraud_rate=0.02, minutes=None, seed=None):
    """Generate one messy batch file and append its labels. Returns (path, raw, labels).
    minutes=N makes a 'live' batch covering only the last N minutes."""
    if seed is not None:
        random.seed(seed)
        np.random.seed(seed)
        Faker.seed(seed)

    INCOMING.mkdir(parents=True, exist_ok=True)
    users = load_or_create_users()

    end = datetime.now().replace(microsecond=0)
    start = end - (timedelta(minutes=minutes) if minutes else timedelta(days=days))

    df = generate_batch(users, n, start, end, fraud_rate)
    raw, labels = mess_up(df)

    path = INCOMING / f"transactions_{end.strftime('%Y%m%d_%H%M%S')}.csv"
    raw.to_csv(path, index=False, encoding="utf-8")
    labels.to_csv(LABELS_FILE, mode="a", header=not LABELS_FILE.exists(),
                  index=False, encoding="utf-8")
    return path, raw, labels


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--n", type=int, default=20000, help="normal transactions")
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--minutes", type=int, default=None,
                   help="live mode: only generate the last N minutes (use 120 or more)")
    p.add_argument("--fraud-rate", type=float, default=0.02)
    p.add_argument("--seed", type=int, default=None)
    args = p.parse_args()

    path, raw, labels = run(args.n, args.days, args.fraud_rate, args.minutes, args.seed)

    print(f"Wrote {len(raw)} rows to {path}")
    print(f"Fraud transactions: {labels.is_fraud.sum()} ({labels.is_fraud.mean():.2%})")
    print(labels[labels.is_fraud == 1].fraud_type.value_counts())


if __name__ == "__main__":
    main()