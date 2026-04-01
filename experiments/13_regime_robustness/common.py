# NO FUTURE DATA IS ALLOWED IN THE SYSTEM. All signals from observable data at decision time only.
"""Experiment 13 — Regime Robustness: experiment-specific config and helpers."""
from __future__ import annotations

from datetime import time as clock_time

from shared.config import SPLIT_THRESHOLD, TC, TRAIN_END, END_DATE
from shared.cursor_engine import Checkpoint, ResolutionMode, build_schedule

EXCLUDE = {"SPY", "QQQ", "VXX"}
SPLIT_THR = SPLIT_THRESHOLD

# Original params (not Optuna — those overfit)
STREAK = 0.75
HR_THR = 0.57
LB = 80
PCTILE = 0.50
MIN_IRET = 0.013

# OOS symbols (324 R1000 symbols not in the training universe)
OOS_SYMBOLS = sorted([
    'A','AAL','ADP','AEP','AFL','AIG','AIZ','AJG','ALL','AMAT','AME','AMT','ANSS','AON',
    'AOS','APD','APH','ARE','ATO','ATVI','BDX','BEN','BIIB','BIO','BK','BLK','BR','BSX',
    'CB','CBOE','CDW','CDNS','CE','CF','CHD','CHRW','CHTR','CI','CINF','CLX','CMA','CME',
    'CMG','CMI','CMS','CNC','CNP','COF','COO','CPRT','CSGP','CSX','CTAS','CTLT','CTSH',
    'CTVA','D','DAL','DD','DFS','DG','DGX','DHI','DLTR','DOV','DPZ','DRI','DTE','DUK',
    'DVA','DVN','DXC','EA','ECL','ED','EFX','EIX','EL','EMN','EMR','ENPH','EPAM','EQIX',
    'EQR','ES','ESS','ETN','ETR','EVRG','EW','EXC','EXPD','EXPE','EXR','FANG','FAST',
    'FBHS','FCX','FDS','FE','FFIV','FIS','FISV','FITB','FLT','FMC','FOX','FOXA','FRC',
    'FRT','FTNT','GD','GIS','GL','GLW','GNRC','GPC','GPN','GRMN','GWW','HBAN','HCA',
    'HOLX','HSIC','HST','HSY','HUM','HWM','ICE','IDXX','IEX','IFF','ILMN','INCY','INVH',
    'IP','IPG','IQV','IR','IRM','ISRG','J','JBHT','JCI','JKHY','JNPR','K','KDP','KEY',
    'KEYS','KHC','KIM','KLAC','KMI','KR','L','LDOS','LEN','LH','LHX','LIN','LKQ','LMT',
    'LNC','LNT','LRCX','LULU','LVS','LW','LYB','LYV','MAA','MAR','MCHP','MCO','MDLZ',
    'MKTX','MLM','MNST','MOH','MOS','MPC','MPWR','MRO','MSCI','MSI','MTB','MTCH','MTD',
    'NCLH','NDAQ','NDSN','NEE','NEM','NI','NOC','NOW','NRG','NSC','NTAP','NTRS','NUE',
    'NVR','ODFL','OKE','OMC','ON','ORCL','ORLY','OTIS','PARA','PAYC','PAYX','PCAR',
    'PEAK','PEG','PFG','PGR','PH','PHM','PKG','PKI','PLD','PNR','PNW','POOL','PPG',
    'PPL','PRU','PSA','PSX','PTC','PVH','PWR','QRVO','RCL','RE','REG','RF','RHI','RJF',
    'RL','RMD','ROK','ROL','ROP','ROST','RSG','SBAC','SEE','SHW','SIVB','SJM','SNA',
    'SNPS','SO','SPG','SPGI','SRE','STE','STT','STX','STZ','SWK','SYF','SYK','SYY',
    'TAP','TDG','TDY','TECH','TEL','TER','TFC','TFX','TGT','TJX','TMUS','TPR','TRGP',
    'TRMB','TROW','TRV','TSCO','TSN','TT','TTWO','TYL','UAL','UDR','UHS','ULTA','UNP',
    'UPS','URI','VFC','VICI','VLO','VMC','VRSK','VRSN','VRTX','VTR','VTRS','WAB','WAT',
    'WBA','WDC','WELL','WM','WMB','WRB','WRK','WST','WTW','WY','WYNN','XEL','XYL',
    'YUM','ZBH','ZBRA','ZION','ZTS',
])

# 12 experiment configs
CONFIGS = {
    # OOS baseline (collapsed)
    "oos_baseline":      {"symbols": "oos", "warmup_days": 20, "use_breadth": False, "breadth_thr": 0, "use_tight_regime": False, "regime_lo": 0.5, "regime_hi": 1.0},
    # Hypothesis 1: More warmup
    "oos_warmup60":      {"symbols": "oos", "warmup_days": 60, "use_breadth": False, "breadth_thr": 0, "use_tight_regime": False, "regime_lo": 0.5, "regime_hi": 1.0},
    "oos_warmup120":     {"symbols": "oos", "warmup_days": 120, "use_breadth": False, "breadth_thr": 0, "use_tight_regime": False, "regime_lo": 0.5, "regime_hi": 1.0},
    "oos_warmup200":     {"symbols": "oos", "warmup_days": 200, "use_breadth": False, "breadth_thr": 0, "use_tight_regime": False, "regime_lo": 0.5, "regime_hi": 1.0},
    # Hypothesis 2: Breadth filter
    "oos_breadth50":     {"symbols": "oos", "warmup_days": 20, "use_breadth": True, "breadth_thr": 0.50, "use_tight_regime": False, "regime_lo": 0.5, "regime_hi": 1.0},
    "oos_breadth55":     {"symbols": "oos", "warmup_days": 20, "use_breadth": True, "breadth_thr": 0.55, "use_tight_regime": False, "regime_lo": 0.5, "regime_hi": 1.0},
    # Hypothesis 3: Tight regime
    "oos_regime99":      {"symbols": "oos", "warmup_days": 20, "use_breadth": False, "breadth_thr": 0, "use_tight_regime": True, "regime_lo": 0.99, "regime_hi": 1.0},
    "oos_regime95":      {"symbols": "oos", "warmup_days": 20, "use_breadth": False, "breadth_thr": 0, "use_tight_regime": True, "regime_lo": 0.95, "regime_hi": 1.0},
    # Combined best
    "oos_combined":      {"symbols": "oos", "warmup_days": 120, "use_breadth": True, "breadth_thr": 0.50, "use_tight_regime": True, "regime_lo": 0.95, "regime_hi": 1.0},
    # Control: TRAINING symbols with same filters
    "train_baseline":    {"symbols": "train", "warmup_days": 20, "use_breadth": False, "breadth_thr": 0, "use_tight_regime": False, "regime_lo": 0.5, "regime_hi": 1.0},
    "train_breadth50":   {"symbols": "train", "warmup_days": 20, "use_breadth": True, "breadth_thr": 0.50, "use_tight_regime": False, "regime_lo": 0.5, "regime_hi": 1.0},
    "train_regime95":    {"symbols": "train", "warmup_days": 20, "use_breadth": False, "breadth_thr": 0, "use_tight_regime": True, "regime_lo": 0.95, "regime_hi": 1.0},
}


def get_schedule():
    return build_schedule("exp13_regime", [
        Checkpoint(name="p0935", target_time_et=clock_time(9, 35),
                   mode=ResolutionMode.AT_OR_BEFORE,
                   grace_minutes_before=5, grace_minutes_after=0,
                   required=False, trading_day_offset=0),
        Checkpoint(name="p1530", target_time_et=clock_time(15, 30),
                   mode=ResolutionMode.AT_OR_BEFORE,
                   grace_minutes_before=5, grace_minutes_after=0,
                   required=False, trading_day_offset=0),
        # p1600 for SPY B&H benchmark (Check 7) and plot_pnl
        Checkpoint(name="p1600", target_time_et=clock_time(16, 0),
                   mode=ResolutionMode.AT_OR_BEFORE,
                   grace_minutes_before=390, grace_minutes_after=0,
                   required=False, trading_day_offset=0),
    ])
