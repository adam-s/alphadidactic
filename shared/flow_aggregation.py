"""Pure aggregation functions over raw options_trades DataFrames.

These helpers take the raw pd.DataFrame output of OptionsTradesSource.fetch()
and produce the specific aggregations each paper needs. No DB access, no state.

The input DataFrame has columns:
    sip_timestamp, option_type, strike, price, size, expiration,
    trade_date_et, minute_et, premium, size_class
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class SizeBucketFlow:
    """Put/call premium grouped by size class."""
    trade_date: object
    symbol: str
    retail_call_premium: float
    retail_put_premium: float
    mid_call_premium: float
    mid_put_premium: float
    inst_call_premium: float
    inst_put_premium: float
    total_call_premium: float
    total_put_premium: float

    @property
    def inst_pc_ratio(self) -> float | None:
        if self.inst_put_premium == 0:
            return None
        return self.inst_call_premium / self.inst_put_premium

    @property
    def total_pc_ratio(self) -> float | None:
        if self.total_put_premium == 0:
            return None
        return self.total_call_premium / self.total_put_premium


@dataclass(frozen=True)
class ExtrinsicFlow:
    """Intrinsic vs extrinsic premium decomposition."""
    trade_date: object
    symbol: str
    call_intrinsic: float
    call_extrinsic: float
    put_intrinsic: float
    put_extrinsic: float
    total_call_premium: float
    total_put_premium: float

    @property
    def extrinsic_direction(self) -> float:
        return self.call_extrinsic - self.put_extrinsic


@dataclass(frozen=True)
class MoneynessFlow:
    """Premium grouped by moneyness class."""
    trade_date: object
    symbol: str
    deep_itm_call_premium: float
    deep_itm_put_premium: float
    itm_call_premium: float
    itm_put_premium: float
    atm_call_premium: float
    atm_put_premium: float
    otm_call_premium: float
    otm_put_premium: float
    deep_otm_call_premium: float
    deep_otm_put_premium: float


def aggregate_by_size_class(
    raw_df: pd.DataFrame,
    *,
    trade_date: object = None,
    symbol: str = "",
) -> SizeBucketFlow:
    """Group raw options trades by size_class and option_type, sum premium."""
    if raw_df.empty:
        return SizeBucketFlow(
            trade_date=trade_date, symbol=symbol,
            retail_call_premium=0, retail_put_premium=0,
            mid_call_premium=0, mid_put_premium=0,
            inst_call_premium=0, inst_put_premium=0,
            total_call_premium=0, total_put_premium=0,
        )

    def _sum(df: pd.DataFrame, size_class: str, option_type: str) -> float:
        mask = (df["size_class"] == size_class) & (df["option_type"] == option_type)
        return float(df.loc[mask, "premium"].sum())

    return SizeBucketFlow(
        trade_date=trade_date,
        symbol=symbol,
        retail_call_premium=_sum(raw_df, "retail", "call"),
        retail_put_premium=_sum(raw_df, "retail", "put"),
        mid_call_premium=_sum(raw_df, "mid", "call"),
        mid_put_premium=_sum(raw_df, "mid", "put"),
        inst_call_premium=_sum(raw_df, "institutional", "call"),
        inst_put_premium=_sum(raw_df, "institutional", "put"),
        total_call_premium=float(raw_df.loc[raw_df["option_type"] == "call", "premium"].sum()),
        total_put_premium=float(raw_df.loc[raw_df["option_type"] == "put", "premium"].sum()),
    )


def compute_extrinsic_premium(
    raw_df: pd.DataFrame,
    spot_price: float,
    *,
    trade_date: object = None,
    symbol: str = "",
) -> ExtrinsicFlow:
    """Decompose each trade's premium into intrinsic and extrinsic components."""
    if raw_df.empty or spot_price <= 0:
        return ExtrinsicFlow(
            trade_date=trade_date, symbol=symbol,
            call_intrinsic=0, call_extrinsic=0,
            put_intrinsic=0, put_extrinsic=0,
            total_call_premium=0, total_put_premium=0,
        )

    df = raw_df.copy()
    df["strike"] = df["strike"].astype(float)
    df["price"] = df["price"].astype(float)
    df["size"] = df["size"].astype(int)

    # Per-contract intrinsic value
    call_mask = df["option_type"] == "call"
    put_mask = df["option_type"] == "put"

    df["intrinsic_per_contract"] = 0.0
    df.loc[call_mask, "intrinsic_per_contract"] = np.maximum(
        spot_price - df.loc[call_mask, "strike"], 0
    )
    df.loc[put_mask, "intrinsic_per_contract"] = np.maximum(
        df.loc[put_mask, "strike"] - spot_price, 0
    )

    df["extrinsic_per_contract"] = np.maximum(df["price"] - df["intrinsic_per_contract"], 0)
    df["intrinsic_premium"] = df["intrinsic_per_contract"] * df["size"] * 100
    df["extrinsic_premium"] = df["extrinsic_per_contract"] * df["size"] * 100

    return ExtrinsicFlow(
        trade_date=trade_date,
        symbol=symbol,
        call_intrinsic=float(df.loc[call_mask, "intrinsic_premium"].sum()),
        call_extrinsic=float(df.loc[call_mask, "extrinsic_premium"].sum()),
        put_intrinsic=float(df.loc[put_mask, "intrinsic_premium"].sum()),
        put_extrinsic=float(df.loc[put_mask, "extrinsic_premium"].sum()),
        total_call_premium=float(df.loc[call_mask, "premium"].sum()),
        total_put_premium=float(df.loc[put_mask, "premium"].sum()),
    )


def compute_moneyness_class(
    raw_df: pd.DataFrame,
    spot_price: float,
    *,
    trade_date: object = None,
    symbol: str = "",
    deep_threshold: float = 0.10,
    atm_threshold: float = 0.02,
) -> MoneynessFlow:
    """Classify each trade by moneyness relative to spot price."""
    if raw_df.empty or spot_price <= 0:
        return MoneynessFlow(
            trade_date=trade_date, symbol=symbol,
            deep_itm_call_premium=0, deep_itm_put_premium=0,
            itm_call_premium=0, itm_put_premium=0,
            atm_call_premium=0, atm_put_premium=0,
            otm_call_premium=0, otm_put_premium=0,
            deep_otm_call_premium=0, deep_otm_put_premium=0,
        )

    df = raw_df.copy()
    df["strike"] = df["strike"].astype(float)
    df["moneyness"] = (df["strike"] - spot_price) / spot_price

    def _classify(row: pd.Series) -> str:
        m = row["moneyness"]
        ot = row["option_type"]
        if ot == "call":
            if m < -deep_threshold:
                return "deep_itm"
            if m < -atm_threshold:
                return "itm"
            if m <= atm_threshold:
                return "atm"
            if m <= deep_threshold:
                return "otm"
            return "deep_otm"
        else:  # put
            if m > deep_threshold:
                return "deep_itm"
            if m > atm_threshold:
                return "itm"
            if m >= -atm_threshold:
                return "atm"
            if m >= -deep_threshold:
                return "otm"
            return "deep_otm"

    df["moneyness_class"] = df.apply(_classify, axis=1)

    def _sum(moneyness_cls: str, option_type: str) -> float:
        mask = (df["moneyness_class"] == moneyness_cls) & (df["option_type"] == option_type)
        return float(df.loc[mask, "premium"].sum())

    return MoneynessFlow(
        trade_date=trade_date, symbol=symbol,
        deep_itm_call_premium=_sum("deep_itm", "call"),
        deep_itm_put_premium=_sum("deep_itm", "put"),
        itm_call_premium=_sum("itm", "call"),
        itm_put_premium=_sum("itm", "put"),
        atm_call_premium=_sum("atm", "call"),
        atm_put_premium=_sum("atm", "put"),
        otm_call_premium=_sum("otm", "call"),
        otm_put_premium=_sum("otm", "put"),
        deep_otm_call_premium=_sum("deep_otm", "call"),
        deep_otm_put_premium=_sum("deep_otm", "put"),
    )


def compute_pc_premium_ratio(
    raw_df: pd.DataFrame,
    *,
    size_class_filter: str | None = None,
) -> float | None:
    """Compute call_premium / put_premium ratio, optionally filtered by size class."""
    if raw_df.empty:
        return None

    df = raw_df
    if size_class_filter is not None:
        df = df[df["size_class"] == size_class_filter]

    call_premium = float(df.loc[df["option_type"] == "call", "premium"].sum())
    put_premium = float(df.loc[df["option_type"] == "put", "premium"].sum())

    if put_premium == 0:
        return None
    return call_premium / put_premium
