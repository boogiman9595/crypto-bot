# ╔══════════════════════════════════════════════════════════════╗
# ║  sl_optimizer.py  —  Smart Stop Loss Placement v1.0          ║
# ║                                                              ║
# ║  PURPOSE: Replace the blind ATR×multiplier SL with a        ║
# ║  structure-aware SL that sits BEYOND the nearest swing       ║
# ║  low (for BUY) or swing high (for SELL), with an ATR        ║
# ║  buffer so market makers can't wick it cleanly.             ║
# ║                                                              ║
# ║  Why this reduces SL hits:                                   ║
# ║  • Blind ATR SL is predictable — MMs know where it is       ║
# ║  • Structure SL is set at a level where price must           ║
# ║    genuinely break the trend to reach it                     ║
# ║  • If price breaks the swing low/high, the trade thesis      ║
# ║    is actually invalid → SL was correct to exit             ║
# ╚══════════════════════════════════════════════════════════════╝

import numpy as np
from typing import Optional
import config


# ══════════════════════════════════════════════════════════════════
# SWING LOW / HIGH DETECTION
# ══════════════════════════════════════════════════════════════════
def _find_swing_low(df, lookback: int = 30) -> Optional[float]:
    """
    Finds the most recent significant swing low in the last `lookback`
    candles. A swing low is a candle whose low is the lowest in a
    ±2 candle window (pivot point).
    Returns the price of that swing low, or None if not found.
    """
    if df is None or len(df) < lookback + 2:
        return None

    data = df.iloc[-lookback:]
    lows = data["low"].values

    # Search from most recent to oldest
    for i in range(len(data) - 3, 1, -1):
        if lows[i] <= min(lows[max(0, i-2):i]) and lows[i] <= min(lows[i+1:min(len(lows), i+3)]):
            return float(lows[i])

    # Fallback: just the lowest low in the window
    return float(min(lows))


def _find_swing_high(df, lookback: int = 30) -> Optional[float]:
    """
    Finds the most recent significant swing high in the last `lookback`
    candles. Returns the price of that swing high, or None if not found.
    """
    if df is None or len(df) < lookback + 2:
        return None

    data = df.iloc[-lookback:]
    highs = data["high"].values

    # Search from most recent to oldest
    for i in range(len(data) - 3, 1, -1):
        if highs[i] >= max(highs[max(0, i-2):i]) and highs[i] >= max(highs[i+1:min(len(highs), i+3)]):
            return float(highs[i])

    # Fallback: just the highest high in the window
    return float(max(highs))


# ══════════════════════════════════════════════════════════════════
# VOLATILITY BUFFER
# ══════════════════════════════════════════════════════════════════
def _atr_buffer(atr_val: float, multiplier: float = 0.3) -> float:
    """
    Returns a small ATR-based buffer to place SL just beyond structure.
    0.3× ATR is enough to avoid normal noise without being too wide.
    """
    return float(atr_val) * multiplier


# ══════════════════════════════════════════════════════════════════
# MAIN SL OPTIMIZER
# ══════════════════════════════════════════════════════════════════
def optimized_sl(
    action: str,
    entry: float,
    atr_sl_price: float,     # the ATR-based SL from strategy.py (fallback)
    atr_val: float,
    df3,                     # 3m candle dataframe for structure detection
    df15=None,               # 15m candle dataframe for higher timeframe structure
) -> tuple[float, str]:
    """
    Returns (optimized_sl_price, method_used).

    Logic:
    1. Find the nearest swing low (BUY) or swing high (SELL) on 3m.
    2. Also check 15m structure if available (higher timeframe S/R is stronger).
    3. Place SL just beyond that structure level + ATR buffer.
    4. Never widen SL beyond config.MAX_SL_PCT from entry (hard cap).
    5. If no structure found or structure SL would be too wide, fall back to ATR SL.

    For BUY:  SL = swing_low - atr_buffer  (below the last low that held)
    For SELL: SL = swing_high + atr_buffer (above the last high that held)
    """
    entry    = float(entry)
    atr_val  = float(atr_val)
    buffer   = _atr_buffer(atr_val, multiplier=0.3)
    max_sl_pct = getattr(config, "MAX_SL_PCT", 0.6)

    structure_sl = None
    method = "atr_fallback"

    if action == "BUY":
        # Try 3m swing low first
        swing_3m = _find_swing_low(df3, lookback=30)
        if swing_3m is not None:
            candidate = swing_3m - buffer
            dist_pct  = abs(entry - candidate) / entry * 100
            if dist_pct <= max_sl_pct:
                structure_sl = candidate
                method = f"swing_low_3m:{round(swing_3m, 4)}"

        # Try 15m swing low — tends to be stronger structure
        if df15 is not None:
            swing_15m = _find_swing_low(df15, lookback=20)
            if swing_15m is not None:
                candidate_15m = swing_15m - buffer
                dist_pct_15m  = abs(entry - candidate_15m) / entry * 100
                if dist_pct_15m <= max_sl_pct:
                    # Prefer 15m if it's tighter than 3m (closer to current price = less risk)
                    if structure_sl is None or candidate_15m > structure_sl:
                        structure_sl = candidate_15m
                        method = f"swing_low_15m:{round(swing_15m, 4)}"

    else:  # SELL
        swing_3m = _find_swing_high(df3, lookback=30)
        if swing_3m is not None:
            candidate = swing_3m + buffer
            dist_pct  = abs(candidate - entry) / entry * 100
            if dist_pct <= max_sl_pct:
                structure_sl = candidate
                method = f"swing_high_3m:{round(swing_3m, 4)}"

        if df15 is not None:
            swing_15m = _find_swing_high(df15, lookback=20)
            if swing_15m is not None:
                candidate_15m = swing_15m + buffer
                dist_pct_15m  = abs(candidate_15m - entry) / entry * 100
                if dist_pct_15m <= max_sl_pct:
                    if structure_sl is None or candidate_15m < structure_sl:
                        structure_sl = candidate_15m
                        method = f"swing_high_15m:{round(swing_15m, 4)}"

    # Final decision: use structure SL if it exists, otherwise keep ATR SL
    if structure_sl is not None:
        final_sl = round(structure_sl, 8)
        print(f"[sl_optimizer] Structure SL used: {final_sl:.4f} via {method}")
        return final_sl, method
    else:
        print(f"[sl_optimizer] ATR fallback SL: {round(atr_sl_price, 4):.4f} (no valid structure found)")
        return round(float(atr_sl_price), 8), "atr_fallback"


# ══════════════════════════════════════════════════════════════════
# TP RECALCULATOR — adjust TPs to maintain R:R after SL change
# ══════════════════════════════════════════════════════════════════
def recalculate_tps_for_sl(
    action: str,
    entry: float,
    new_sl: float,
) -> tuple[float, float, float, float]:
    """
    After the SL is moved by sl_optimizer, recalculate TP1/2/3
    so the R:R ratios are maintained relative to the new SL distance.

    Returns (tp1, tp2, tp3, sl_pct).
    Uses config ATR multipliers but scales them to the new SL distance.
    """
    entry    = float(entry)
    new_sl   = float(new_sl)
    sl_dist  = abs(entry - new_sl)

    # Scale TP distances proportionally to the new SL distance
    # Using the config ratio:  TP1 = 1.5× SL dist, TP2 = 2.8/0.8 × SL, TP3 = 5.0/0.8 × SL
    atr_sl  = getattr(config, "ATR_SL",  0.8)
    atr_tp1 = getattr(config, "ATR_TP1", 1.5)
    atr_tp2 = getattr(config, "ATR_TP2", 2.8)
    atr_tp3 = getattr(config, "ATR_TP3", 5.0)

    tp1_dist = sl_dist * (atr_tp1 / atr_sl)
    tp2_dist = sl_dist * (atr_tp2 / atr_sl)
    tp3_dist = sl_dist * (atr_tp3 / atr_sl)

    if action == "BUY":
        tp1 = entry + tp1_dist
        tp2 = entry + tp2_dist
        tp3 = entry + tp3_dist
    else:
        tp1 = entry - tp1_dist
        tp2 = entry - tp2_dist
        tp3 = entry - tp3_dist

    sl_pct = abs(entry - new_sl) / entry * 100

    return (
        round(tp1, 8),
        round(tp2, 8),
        round(tp3, 8),
        round(sl_pct, 4),
    )
