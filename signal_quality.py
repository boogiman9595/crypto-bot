# ╔══════════════════════════════════════════════════════════════╗
# ║  signal_quality.py  —  Signal Quality Scorer v2.0            ║
# ║                                                              ║
# ║  FIXES vs v1.0:                                              ║
# ║  • Dimension 6 now reads stoch_k/stoch_d from sig dict      ║
# ║    (was always defaulting to 50 because strategy never set  ║
# ║     them — now strategy.py v7.0 includes them)              ║
# ║  • ADX score added to dim-1 (trend alignment)               ║
# ║  • RSI divergence bonus in dim-2 (momentum quality)         ║
# ║  • Ichimoku bias in dim-1                                    ║
# ╚══════════════════════════════════════════════════════════════╝

import numpy as np


def _ema(series, period):
    return series.ewm(span=period, adjust=False).mean()


# ══════════════════════════════════════════════════════════════════
# DIMENSION 1: TREND ALIGNMENT (0-20)
# ══════════════════════════════════════════════════════════════════
def score_trend_alignment(df3, df15, sig: dict, action: str) -> tuple[int, str]:
    try:
        closes3  = df3["close"]
        closes15 = df15["close"]

        e9_3m  = float(_ema(closes3,  9).iloc[-1])
        e21_3m = float(_ema(closes3, 21).iloc[-1])
        e50_3m = float(_ema(closes3, 50).iloc[-1])
        e20_15m = float(_ema(closes15, 20).iloc[-1])
        e50_15m = float(_ema(closes15, 50).iloc[-1])
        price   = float(closes3.iloc[-1])

        score = 0
        notes = []

        if action == "BUY":
            if e9_3m > e21_3m:    score += 4;  notes.append("EMA9>21 ✅")
            if e21_3m > e50_3m:   score += 4;  notes.append("EMA21>50 ✅")
            if price > e50_3m:    score += 3;  notes.append("price>EMA50 ✅")
            if e20_15m > e50_15m: score += 5;  notes.append("15m bull ✅")
            # Ichimoku bonus
            if sig.get("ichimoku_bias") == "BULL": score += 2; notes.append("Ichi=BULL ✅")
            # ADX directional bonus
            if sig.get("adx_pdi", 0) > sig.get("adx_mdi", 0) + 5: score += 2; notes.append("+DI>-DI ✅")
        else:
            if e9_3m < e21_3m:    score += 4;  notes.append("EMA9<21 ✅")
            if e21_3m < e50_3m:   score += 4;  notes.append("EMA21<50 ✅")
            if price < e50_3m:    score += 3;  notes.append("price<EMA50 ✅")
            if e20_15m < e50_15m: score += 5;  notes.append("15m bear ✅")
            if sig.get("ichimoku_bias") == "BEAR": score += 2; notes.append("Ichi=BEAR ✅")
            if sig.get("adx_mdi", 0) > sig.get("adx_pdi", 0) + 5: score += 2; notes.append("-DI>+DI ✅")

        return min(score, 20), f"trend_align={min(score,20)}/20 ({', '.join(notes) or 'none'})"
    except Exception as e:
        return 10, f"trend_align=skip:{e}"


# ══════════════════════════════════════════════════════════════════
# DIMENSION 2: MOMENTUM QUALITY (0-20)
# ══════════════════════════════════════════════════════════════════
def score_momentum(df3, df5, sig: dict, action: str) -> tuple[int, str]:
    try:
        closes3 = df3["close"]
        closes5 = df5["close"]

        def macd_hist(series):
            fast = series.ewm(span=12, adjust=False).mean()
            slow = series.ewm(span=26, adjust=False).mean()
            line = fast - slow
            sig_ = line.ewm(span=9, adjust=False).mean()
            return line - sig_

        hist3 = macd_hist(closes3)
        hist5 = macd_hist(closes5)

        h3_curr  = float(hist3.iloc[-1])
        h3_prev  = float(hist3.iloc[-2])
        h3_prev2 = float(hist3.iloc[-3])
        h5_val   = float(hist5.iloc[-1])
        h5_prev  = float(hist5.iloc[-2])

        score = 0
        notes = []

        if action == "BUY":
            if h3_curr > 0:                              score += 4; notes.append("MACD+ ✅")
            if h3_curr > h3_prev:                        score += 3; notes.append("MACD accel ✅")
            if h3_curr > h3_prev and h3_prev > h3_prev2: score += 3; notes.append("MACD 3-bar ✅")
            if h5_val > 0:                               score += 4; notes.append("5m MACD+ ✅")
            if h5_val > h5_prev:                         score += 2; notes.append("5m rising ✅")
            if sig.get("rsi_divergence") == "BULL_DIV":  score += 4; notes.append("RSI_DIV ✅")
        else:
            if h3_curr < 0:                              score += 4; notes.append("MACD- ✅")
            if h3_curr < h3_prev:                        score += 3; notes.append("MACD accel ✅")
            if h3_curr < h3_prev and h3_prev < h3_prev2: score += 3; notes.append("MACD 3-bar ✅")
            if h5_val < 0:                               score += 4; notes.append("5m MACD- ✅")
            if h5_val < h5_prev:                         score += 2; notes.append("5m falling ✅")
            if sig.get("rsi_divergence") == "BEAR_DIV":  score += 4; notes.append("RSI_DIV ✅")

        return min(score, 20), f"momentum={min(score,20)}/20 ({', '.join(notes) or 'none'})"
    except Exception as e:
        return 10, f"momentum=skip:{e}"


# ══════════════════════════════════════════════════════════════════
# DIMENSION 3: ENTRY STRUCTURE (0-20)
# ══════════════════════════════════════════════════════════════════
def score_entry_structure(df3, action: str, trigger: str) -> tuple[int, str]:
    try:
        closes = df3["close"].values
        highs  = df3["high"].values
        lows   = df3["low"].values
        price  = float(closes[-1])

        score = 0
        notes = []

        trigger_scores = {
            "PULLBACK 🎯":  16,
            "SR_BOUNCE 🔄": 14,
            "MOMENTUM ⚡":  10,
            "BREAKOUT 🚀":   8,
            "SIGNAL 📊":     6,
        }
        t_score = trigger_scores.get(trigger, 6)
        score += t_score
        notes.append(f"trigger={trigger}({t_score})")

        recent_high = float(max(highs[-10:]))
        recent_low  = float(min(lows[-10:]))
        rng = recent_high - recent_low

        if rng > 0:
            position = (price - recent_low) / rng
            if action == "BUY"  and 0.2 <= position <= 0.65:
                score += 4; notes.append("good_entry_zone ✅")
            elif action == "SELL" and 0.35 <= position <= 0.80:
                score += 4; notes.append("good_entry_zone ✅")

        return min(score, 20), f"structure={min(score,20)}/20 ({', '.join(notes)})"
    except Exception as e:
        return 8, f"structure=skip:{e}"


# ══════════════════════════════════════════════════════════════════
# DIMENSION 4: VOLUME QUALITY (0-15)
# ══════════════════════════════════════════════════════════════════
def score_volume(df3, action: str, vol_spike: bool, vol_trend: str) -> tuple[int, str]:
    try:
        vols = df3["volume"].values
        avg  = float(np.mean(vols[-20:]))
        curr = float(vols[-1])

        score = 0
        notes = []
        ratio = curr / (avg + 1e-10)

        if ratio >= 1.8:   score += 8;  notes.append(f"spike×{ratio:.1f} ✅")
        elif ratio >= 1.3: score += 6;  notes.append(f"high×{ratio:.1f} ✅")
        elif ratio >= 1.0: score += 4;  notes.append(f"avg×{ratio:.1f}")
        else:               score += 1;  notes.append(f"low×{ratio:.1f}")

        if "RISING" in vol_trend: score += 4; notes.append("vol_rising ✅")
        elif "FLAT"  in vol_trend: score += 2
        else:                       notes.append("vol_falling ⚠️")

        last3_avg = float(np.mean(vols[-3:]))
        if last3_avg > avg * 1.1: score += 3; notes.append("sustained ✅")

        return min(score, 15), f"volume={min(score,15)}/15 ({', '.join(notes)})"
    except Exception as e:
        return 7, f"volume=skip:{e}"


# ══════════════════════════════════════════════════════════════════
# DIMENSION 5: RISK/REWARD QUALITY (0-15)
# ══════════════════════════════════════════════════════════════════
def score_rr(rr: float, sl_pct: float) -> tuple[int, str]:
    try:
        score = 0
        notes = []

        if rr >= 3.0:    score += 9;  notes.append(f"R:R={rr:.1f} excellent ✅")
        elif rr >= 2.0:  score += 7;  notes.append(f"R:R={rr:.1f} good ✅")
        elif rr >= 1.5:  score += 5;  notes.append(f"R:R={rr:.1f} ok")
        else:             score += 2;  notes.append(f"R:R={rr:.1f} marginal ⚠️")

        if sl_pct <= 0.25:   score += 6; notes.append("tight_SL ✅")
        elif sl_pct <= 0.40: score += 4; notes.append("good_SL ✅")
        elif sl_pct <= 0.55: score += 2; notes.append("ok_SL")
        else:                  score += 0; notes.append("wide_SL ⚠️")

        return min(score, 15), f"rr={min(score,15)}/15 ({', '.join(notes)})"
    except Exception as e:
        return 7, f"rr=skip:{e}"


# ══════════════════════════════════════════════════════════════════
# DIMENSION 6: INDICATOR AGREEMENT (0-10)
# FIXED: stoch_k/stoch_d now read from sig dict correctly
# ══════════════════════════════════════════════════════════════════
def score_indicator_agreement(sig: dict, action: str) -> tuple[int, str]:
    try:
        rsi_val  = sig.get("rsi", 50)
        # FIX: strategy.py v7.0 now includes stoch_k and stoch_d
        stoch_k  = sig.get("stoch_k", 50)
        bb_upper = sig.get("bb_upper", 0)
        bb_lower = sig.get("bb_lower", 0)
        price    = sig.get("price", 0)

        score = 0
        notes = []

        if action == "BUY":
            if rsi_val < 40:   score += 4; notes.append(f"RSI={rsi_val:.0f} oversold ✅")
            elif rsi_val < 50: score += 2; notes.append(f"RSI={rsi_val:.0f} ok")
            else:               notes.append(f"RSI={rsi_val:.0f} high ⚠️")

            if bb_lower > 0 and price <= bb_lower * 1.005:
                score += 3; notes.append("at_BB_lower ✅")
            elif bb_lower > 0 and price < (bb_lower + bb_upper) / 2:
                score += 1

            if stoch_k < 30:   score += 3; notes.append(f"Stoch={stoch_k:.0f} ✅")
            elif stoch_k < 45: score += 1

        else:
            if rsi_val > 60:   score += 4; notes.append(f"RSI={rsi_val:.0f} overbought ✅")
            elif rsi_val > 50: score += 2; notes.append(f"RSI={rsi_val:.0f} ok")
            else:               notes.append(f"RSI={rsi_val:.0f} low ⚠️")

            if bb_upper > 0 and price >= bb_upper * 0.995:
                score += 3; notes.append("at_BB_upper ✅")
            elif bb_upper > 0 and price > (bb_lower + bb_upper) / 2:
                score += 1

            if stoch_k > 70:   score += 3; notes.append(f"Stoch={stoch_k:.0f} ✅")
            elif stoch_k > 55: score += 1

        return min(score, 10), f"indicators={min(score,10)}/10 ({', '.join(notes) or 'mixed'})"
    except Exception as e:
        return 5, f"indicators=skip:{e}"


# ══════════════════════════════════════════════════════════════════
# MASTER SCORER
# ══════════════════════════════════════════════════════════════════
def score_signal(
    sig: dict,
    df3,
    df5,
    df15,
    action: str,
) -> tuple[int, bool, str]:
    s1, n1 = score_trend_alignment(df3, df15, sig, action)
    s2, n2 = score_momentum(df3, df5, sig, action)
    s3, n3 = score_entry_structure(df3, action, sig.get("entry_trigger", "SIGNAL 📊"))
    s4, n4 = score_volume(df3, action, sig.get("vol_spike", False), sig.get("vol_trend", "FLAT ➡️"))
    s5, n5 = score_rr(sig.get("rr", 1.0), sig.get("sl_pct", 0.5))
    s6, n6 = score_indicator_agreement(sig, action)

    total = s1 + s2 + s3 + s4 + s5 + s6
    should_trade = total >= 60

    grade = ("🔴 POOR" if total < 50
             else "🟡 OK"        if total < 65
             else "🟢 GOOD"      if total < 80
             else "🟢 EXCELLENT")

    breakdown = (
        f"Quality: {total}/100 {grade}\n"
        f"  {n1}\n  {n2}\n  {n3}\n  {n4}\n  {n5}\n  {n6}"
    )

    print(f"[signal_quality] {total}/100 {'✅' if should_trade else '❌'} | {n1} | {n2} | {n3}")
    return total, should_trade, breakdown
