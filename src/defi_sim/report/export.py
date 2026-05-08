"""Data export utilities — CSV, JSON, Parquet."""

from __future__ import annotations


import pandas as pd


def to_csv(df: pd.DataFrame, path: str) -> None:
    """Write results to CSV."""
    df.to_csv(path, index=False)


def to_json(df: pd.DataFrame, path: str, fields: list[str] | None = None) -> None:
    """Write results to JSON with optional field selection."""
    if fields:
        df = df[fields]
    df.to_json(path, orient="records", indent=2)


def to_parquet(df: pd.DataFrame, path: str, compression: str = "snappy") -> None:
    """Write results to Parquet. Preferred for datasets > 10k rows."""
    df.to_parquet(path, compression=compression, index=False)


def from_parquet(path: str) -> pd.DataFrame:
    """Read back sweep results from Parquet."""
    return pd.read_parquet(path)
