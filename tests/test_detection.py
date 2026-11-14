import numpy as np
import pandas as pd

from src.detection.features import window_sum


def _df():
    return pd.DataFrame({"sender_upi": ["a", "a", "a", "b"],
                         "ts_s": [0.0, 100.0, 1000.0, 50.0]})


def test_window_sum_backward_is_per_sender():
    out = window_sum(_df(), "sender_upi", np.ones(4), 300, 0)
    assert out.tolist() == [1, 2, 1, 1]


def test_window_sum_forward():
    out = window_sum(_df(), "sender_upi", np.ones(4), 0, 300)
    assert out.tolist() == [2, 1, 1, 1]