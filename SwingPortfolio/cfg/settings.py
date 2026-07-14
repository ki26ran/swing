CAPITAL_TOTAL = 1500000
MAX_POSITIONS_PER_STRATEGY = 2
MAX_POSITIONS_TOTAL = 6
CAPITAL_MODE = "equal"
# Weights for "weighted" mode (unused when CAPITAL_MODE == "equal")
STRATEGY_WEIGHTS = {
    "donchian_adx": 0.34,
    "keltner_rsi": 0.33,
    "supertrend_volume": 0.33,
}

# Position sizing mode
POSITION_SIZING = "options"  # "lot" (equity), "options" (CE/PE), or "capital"
OPTION_STRIKE = "ATM"     # "ATM" or "ITM1"
PRODUCT_TYPE = "NRML"     # "I" (MIS/intraday), "M" (NRML/margin), "C" (CNC)
MAX_MARGIN_PER_STOCK = 200000
MARGIN_RATE = 0.15

TRAIL_PCT = 0.015
SL_ATR_MULTIPLIER = 3.0
RR_RATIO = 1.5
HARD_SL_AMOUNT = 50000
HARD_SL_ENABLED = True
GAP_FILTER_ENABLED = True
REGIME_FILTER_ENABLED = True

ENTRY_TIME = "zscore"
FALLBACK_CUTOFF = "10:30"  # If z-score not triggered by this time, enter at market

SHEET_ENABLED = False
SHEET_POLL_INTERVAL = 60
