import numpy as np

BIG_AMOUNT = 20_000
ODD_HOUR_MIN_AMOUNT = 10_000

# name -> (weight, function returning a True/False Series)
RULES = {
    "velocity": (40, lambda f: (f.tx_5m_back >= 10) | (f.tx_5m_fwd >= 10)),
    "amount_spike": (30, lambda f: (f.txn_count_user >= 3) & (f.amount_ratio > 5)),
    "new_payee_high": (45, lambda f: f.is_first_pair & (f.amount >= BIG_AMOUNT)),
    "mule_fan_in": (40, lambda f: f.fan_in_senders_1h >= 8),
    "mule_outflow": (70, lambda f: f.mule_outflow_flag),
    "odd_hours": (35, lambda f: f.hour_ist.between(1, 3) & (f.amount >= ODD_HOUR_MIN_AMOUNT)),
    "failed_then_success": (45, lambda f:
        ((f.status == "SUCCESS") & (f.failed_10m_back >= 3))
        | ((f.status == "FAILED") & (f.failed_10m_around >= 3) & (f.success_10m_fwd >= 1))),
    "device_location_change": (45, lambda f: f.device_changed & f.location_changed),
    "round_amount": (40, lambda f: f.near_limit & (f.near_limit_1h >= 3)),
}


def apply_rules(f):
    """Returns (rule_score 0-100 as array, list of triggered rule names per row)."""
    names = list(RULES)
    weights = np.array([w for w, _ in RULES.values()], dtype=float)
    hits = np.column_stack([np.asarray(fn(f), dtype=bool) for _, fn in RULES.values()])
    rule_score = np.minimum(100.0, hits @ weights)
    triggered = [[n for n, h in zip(names, row) if h] for row in hits]
    return rule_score, triggered