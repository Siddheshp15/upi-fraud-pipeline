import pytest

from src.evaluation.evaluate import summarize


def test_summarize_basic():
    s = summarize(tp=8, n_alerts=10, positives=16, negatives=984)
    assert s["fp"] == 2 and s["fn"] == 8
    assert s["precision"] == pytest.approx(0.8)
    assert s["recall"] == pytest.approx(0.5)
    assert s["fpr"] == pytest.approx(2 / 984)


def test_summarize_no_alerts():
    s = summarize(tp=0, n_alerts=0, positives=5, negatives=95)
    assert s["precision"] == 0.0 and s["recall"] == 0.0 and s["f1"] == 0.0