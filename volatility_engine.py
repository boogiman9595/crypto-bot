# ╔══════════════════════════════════════════════════════════════╗
# ║  volatility_engine.py  —  Volatility Regime Classifier v1.0 ║
# ║                                                              ║
# ║  PURPOSE: Classify current market volatility regime and     ║
# ║  adjust position sizing + TP/SL multipliers accordingly.    ║
# ║                                                              ║
# ║  Regimes:                                                    ║
# ║  EXPLOSIVE  — ATR > 2× 20-period avg → breakout mode       ║
# ║  HIGH       — ATR 1.4-2.0× avg      → trending, full size  ║
# ║  NORMAL     — ATR 0.8-1.4× avg      → standard conditions  ║
# ║  LOW        — ATR < 0.8× avg        → chop/squeeze         ║
# ║  DEAD       — ATR < 0.3× avg        → dead market, avoid   ║
# ║                                                              ║
# ║  Outputs:                                                    ║
# ║  • size_multiplier: scale position size up/down             ║
# ║  • tp_multiplier:   extend TPs in explosive regimes         ║
# ║  • sl_multiplier:   widen SL slightly in explosive markets  ║
# ║  • tradeable:       False in DEAD regime                    ║
# ╚══════════════════════════════════════════════════════════════╝

import numpy as np
from typing import Optional


# ── ATR ratio thresholds (current ATR / 20-period ATR average)
REGIME_THRESHOLDS = {
    "EXPLOSIVE": 2.0,
    "HIGH":      1.4,
    "NORMAL":    0.8,
    "LOW":       0.3,
    # Below 0.3 = DEAD
}

# ── Per-regime trading parameters
REGIME_PARAMS = {
    "EXPLOSIVE": {
        "size_mult":  0.70,   # smaller size — explosive moves = larger SL needed
        "tp_mult":    1.40,   # extend TPs — bigger moves possible
        "sl_mult":    1.30,   # wider SL — wicks are massive in explosions
        "tradeable":  True,
        "label":      "🔥 EXPLOSIVE",
        "confidence_mult": 1.05,  # slight boost — momentum trades
    },
    "HIGH": {
        "size_mult":  1.0,
        "tp_mult":    1.10,
        "sl_mult":    1.0,
        "tradeable":  True,
        "label":      "📈 HIGH VOL",
        "confidence_mult": 1.0,
    },
    "NORMAL": {
        "size_mult":  1.0,
        "tp_mult":    1.0,
        "sl_mult":    1.0,
        "tradeable":  True,
        "label":      "⚖️ NORMAL",
        "confidence_mult": 1.0,
    },
    "LOW": {
        "size_mult":  0.80,   # smaller — choppy, more fake-outs
        "tp_mult":    0.85,   # tighter TPs — moves don't extend
        "sl_mult":    0.90,   # tighter SL also
        "tradeable":  True,
        "label":      "😴 LOW VOL",
        "confidence_mult": 0.90,
    },
    "DEAD": {
        "size_mult":  0.0,
        "tp_mult":    1.0,
        "sl_mult":    1.0,
        "tradeable":  False,
        "label":      "💀 DEAD",
        "confidence_mult": 0.0,
    },
}


def classify_volatility(df, atr_period: int = 14, avg_period: int = 20) -> dict:
    """
    Classifies the current ATR regime and returns trading parameters.

    Args:
        df:          OHLCV DataFrame (3m or 15m candles)
        atr_period:  ATR calculation period
        avg_period:  how many ATR values to average for baseline

    Returns dict with:
        regime:          "EXPLOSIVE" | "HIGH" | "NORMAL" | "LOW" | "DEAD"
        atr_current:     float — current ATR
        atr_avg:         float — 20-bar ATR average (baseline)
        atr_ratio:       float — current/avg ratio
        size_mult:       float — multiply position size
        tp_mult:         float — multiply TP distances
        sl_mult:         float — multiply SL distance
        tradeable:       bool
        label:           display string
        confidence_mult: float — apply to signal confidence
    """
    try:
        if df is None or len(df) < atr_period + avg_period + 5:
            return _default_regime()

        # ATR calculation
        hl  = df["high"] - df["low"]
        hc  = (df["high"] - df["close"].shift()).abs()
        lc  = (df["low"]  - df["close"].shift()).abs()
        tr  = np.maximum(hl.values, np.maximum(hc.values, lc.values))

        # EWM ATR (same as strategy.py)
        atr_series = _ewm_atr(tr, atr_period)

        if len(atr_series) < avg_period + 2:
            return _default_regime()

        atr_current = float(atr_series[-1])
        atr_avg     = float(np.mean(atr_series[-avg_period:]))

        if atr_avg < 1e-10:
            return _default_regime()

        atr_ratio = atr_current / atr_avg

        # Classify regime
        if atr_ratio >= REGIME_THRESHOLDS["EXPLOSIVE"]:
            regime = "EXPLOSIVE"
        elif atr_ratio >= REGIME_THRESHOLDS["HIGH"]:
            regime = "HIGH"
        elif atr_ratio >= REGIME_THRESHOLDS["NORMAL"]:
            regime = "NORMAL"
        elif atr_ratio >= REGIME_THRESHOLDS["LOW"]:
            regime = "LOW"
        else:
            regime = "DEAD"

        params = REGIME_PARAMS[regime].copy()
        params.update({
            "regime":      regime,
            "atr_current": round(atr_current, 8),
            "atr_avg":     round(atr_avg, 8),
            "atr_ratio":   round(atr_ratio, 3),
        })
        return params

    except Exception as e:
        print(f"[volatility_engine] error: {e}")
        return _default_regime()


def _ewm_atr(tr_array, period):
    """Compute EWM ATR without pandas (faster on numpy arrays)."""
    alpha = 2.0 / (period + 1)
    result = []
    prev = float(np.mean(tr_array[:period]))
    for val in tr_array:
        prev = alpha * float(val) + (1 - alpha) * prev
        result.append(prev)
    return result


def _default_regime() -> dict:
    """Returns NORMAL regime when data is unavailable."""
    params = REGIME_PARAMS["NORMAL"].copy()
    params.update({
        "regime":      "NORMAL",
        "atr_current": 0.0,
        "atr_avg":     0.0,
        "atr_ratio":   1.0,
    })
    return params


def apply_volatility_adjustments(
    tp1: float,
    tp2: float,
    tp3: float,
    sl: float,
    entry: float,
    signal: str,
    regime_params: dict,
) -> tuple[float, float, float, float]:
    """
    Adjusts TP and SL levels based on the volatility regime multipliers.
    Only modifies if regime is EXPLOSIVE or LOW — NORMAL/HIGH unchanged.

    Returns (tp1, tp2, tp3, sl)
    """
    tp_mult = regime_params.get("tp_mult", 1.0)
    sl_mult = regime_params.get("sl_mult", 1.0)

    # No adjustment needed for normal conditions
    if tp_mult == 1.0 and sl_mult == 1.0:
        return tp1, tp2, tp3, sl

    tp1_dist = abs(tp1 - entry)
    tp2_dist = abs(tp2 - entry)
    tp3_dist = abs(tp3 - entry)
    sl_dist  = abs(sl  - entry)

    if signal == "BUY":
        new_tp1 = entry + tp1_dist * tp_mult
        new_tp2 = entry + tp2_dist * tp_mult
        new_tp3 = entry + tp3_dist * tp_mult
        new_sl  = entry - sl_dist  * sl_mult
    else:
        new_tp1 = entry - tp1_dist * tp_mult
        new_tp2 = entry - tp2_dist * tp_mult
        new_tp3 = entry - tp3_dist * tp_mult
        new_sl  = entry + sl_dist  * sl_mult

    return (
        round(new_tp1, 8),
        round(new_tp2, 8),
        round(new_tp3, 8),
        round(new_sl,  8),
    )


def format_regime_line(regime_params: dict) -> str:
    """One-line summary for Telegram signal messages."""
    return (
        f"{regime_params['label']} "
        f"(ATR ratio: {regime_params['atr_ratio']:.2f}× avg)"
    )
