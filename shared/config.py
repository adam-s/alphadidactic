"""
config.py — Constants, instrument pairs, dates, FRED series config.

Single source of truth for the ATM decomposition + FRED research stream.
"""

import os

# Database
DB_URL = os.environ.get("DATABASE_URL", "")

# Date boundaries
START_DATE = "2022-01-18"
END_DATE = "2026-02-28"
TRAIN_END = "2024-12-31"   # walk-forward split

# Starting capital for backtests
INITIAL_CAPITAL = 10_000

# Transaction cost per side (2bps)
TC = 0.0002

# Split protection threshold
SPLIT_THRESHOLD = 0.20


def is_split(r: float) -> bool:
    """Symmetric split filter: True if |return| exceeds threshold."""
    return abs(r) >= SPLIT_THRESHOLD

# Size class boundaries (options_trades.size)
SIZE_RETAIL = (1, 9)
SIZE_MID = (10, 99)
SIZE_INST = (100, None)  # 100+

# Instrument pairs: signal source -> leveraged execution vehicles
PAIRS = {
    "SPY": {"bull": "SPXL", "bear": "SPXS"},
    "QQQ": {"bull": "TQQQ", "bear": "SQQQ"},
    "TSLA": {"bull": "TSLL", "bear": "TSLQ"},
}

# All symbols we need prices for (signal + vehicles)
ALL_SYMBOLS = ["SPY", "QQQ", "TSLA", "SPXL", "SPXS", "TQQQ", "SQQQ", "TSLL", "TSLQ"]

# Strike increments per underlying (for ATM calculation)
STRIKE_INCREMENTS = {
    "SPY": 1.0,
    "QQQ": 1.0,
    "TSLA": 1.0,
}

# Default ATM band: +/- N strikes from ATM
DEFAULT_ATM_STRIKES = 2

# Default feature window (Eastern Time)
DEFAULT_WINDOW_START = "10:00"
DEFAULT_WINDOW_END = "11:00"

# ════════════════════════════════════════════════════════════════════════════════
# FRED CONFIGURATION
# ════════════════════════════════════════════════════════════════════════════════

# FRED series we track in the database
FRED_SERIES = {
    # Daily (HIGH priority for MS-VAR)
    "T10Y2Y": "10Y-2Y Treasury Spread (yield curve slope)",
    "BAMLH0A0HYM2": "ICE BofA US HY Option-Adjusted Spread",
    "DFF": "Federal Funds Effective Rate (daily)",
    "DGS10": "10-Year Treasury Constant Maturity Rate",
    "DGS2": "2-Year Treasury Constant Maturity Rate",
    "DTWEXBGS": "Trade-Weighted US Dollar Index",
    "TEDRATE": "TED Spread (3-month LIBOR vs T-bill)",
    "T10YIE": "10-Year Breakeven Inflation Rate",
    "VIXCLS": "VIX (CBOE Volatility Index)",
    # Weekly
    "ICSA": "Initial Jobless Claims",
    # Monthly
    "FEDFUNDS": "Federal Funds Rate",
    "CPIAUCSL": "Consumer Price Index (All Urban)",
    "PAYEMS": "Total Nonfarm Payrolls",
    "UNRATE": "Unemployment Rate",
    "INDPRO": "Industrial Production Index",
    "UMCSENT": "U Michigan Consumer Sentiment",
    "HOUST": "Housing Starts",
    "RSAFS": "Retail Sales",
    "PCE": "Personal Consumption Expenditures",
    "PCEPI": "PCE Price Index",
    # Quarterly
    "GDP": "Gross Domestic Product",
    "GDPC1": "Real GDP",
}

# Publication lag map: how many BUSINESS DAYS after observation date
# the value is actually available to the public.
# Conservative defaults — better to be too cautious than to introduce look-ahead.
PUBLICATION_LAGS = {
    # Daily series: published next business day (T+1)
    "T10Y2Y": 1,
    "BAMLH0A0HYM2": 1,
    "DFF": 1,
    "DGS10": 1,
    "DGS2": 1,
    "DTWEXBGS": 1,
    "TEDRATE": 1,
    "T10YIE": 1,
    "VIXCLS": 1,
    # Weekly: released Thursday for prior week
    "ICSA": 5,
    # Monthly: various release schedules
    "FEDFUNDS": 10,    # ~10th of next month
    "CPIAUCSL": 15,    # ~15th of next month
    "PAYEMS": 7,       # first Friday of next month
    "UNRATE": 7,       # released with PAYEMS
    "INDPRO": 18,      # ~18th of next month
    "UMCSENT": 2,      # preliminary mid-month, final end-month
    "HOUST": 18,       # ~18th of next month
    "RSAFS": 15,       # ~15th of next month
    "PCE": 25,         # ~25th of next month
    "PCEPI": 25,       # released with PCE
    # Quarterly: released ~30 days after quarter end
    "GDP": 30,
    "GDPC1": 30,
}

# Default lag for any series not in the map
DEFAULT_PUBLICATION_LAG = 20

# ════════════════════════════════════════════════════════════════════════════════
# TICKER / CORPORATE ACTION CONFIGURATION
# ════════════════════════════════════════════════════════════════════════════════

from datetime import date

# Tickers that changed meaning — filter rows before this date for the given symbol.
# META: Facebook renamed from FB to META on 2022-06-09; options_trades has unrelated
# "META" tickers before that date.
TICKER_VALID_FROM: dict[str, date] = {
    "META": date(2022, 6, 9),
}

# VXX reverse splits — ratio is the multiplier applied to share count.
# e.g., 0.25 means 4:1 reverse split (shares divided by 4, price multiplied by 4).
VXX_SPLITS: dict[date, float] = {
    date(2023, 3, 7): 0.25,
    date(2024, 7, 24): 0.25,
}

# Sector ETFs for rotation strategies
SECTOR_ETFS = ["XLE", "XLF", "XLV", "XLI", "XLP", "XLY", "XLK"]

# ETF universe (symbols that are ETFs, not individual stocks)
ETF_SYMBOLS = {
    "SPY", "QQQ", "IWM", "XLK", "XLF", "XLV", "XLE", "XLI", "XLP", "XLY",
    "XLC", "XLRE", "XLB", "XLU", "VXX", "UNG", "GLD", "TLT", "HYG", "SH",
    "SQQQ", "SDS", "RWM", "SPXL", "SPXS", "TQQQ", "TSLL", "TSLQ",
    "TNA", "TZA", "SSO", "QLD", "QID", "SOXL", "SOXS",
}
