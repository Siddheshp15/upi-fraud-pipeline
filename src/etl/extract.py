import pandas as pd
from src.config import INCOMING


def list_new_files():
    return sorted(INCOMING.glob("*.csv"))


def read_file(path):
    # everything as text: parsing and validation happen in transform
    return pd.read_csv(path, dtype=str, encoding="utf-8")