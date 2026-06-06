# ╔══════════════════════════════════════════════════════════════╗
# ║  strategy.py  —  Signal Engine v7.0                          ║
# ║                                                              ║
# ║  UPGRADES vs v6.2:                                           ║
# ║  • ADX filter — blocks entries in directionless markets     ║
# ║  • stoch_k / stoch_d now included in signal dict            ║
# ║    (fixes signal_quality.py dim-6 always defaulting to 50)  ║
# ║  • RSI divergence detection — flags hidden bull/bear diverg ║
# ║  • Ichimoku cloud quick check on 15m for trend bias          ║
# ║  • ATR % gate — skip coins with < 0.05% ATR (dead market)  ║
# ║  • score now exported as both raw int and net (bull-bear)   ║
# ╚══════════════════════════════════════════════════════════════╝

import ccxt
import pandas as pd
import numpy as np
import config

exchange = ccxt.binance({"enableRateLimit": True})

def get_data(symbol, timeframe, limit=None):
    limit = limit or config.CANDLE_LIMIT
    try:
        bars = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        if not bars or len(bars) < 10:
            return None
        df = pd.DataFrame(bars,
             columns=["time","open","high","low","close","volume"])
        for col in ["open","high","low","close","volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df.dropna(inplace=True)
        df.reset_index(drop=True, inplace=True)
        return df
    except Exception as e:
        print(f"[get_data] {symbol} {timeframe}: {e}")
        return None

def get_live_price(symbol):
    try:
        ticker = exchange.fetch_ticker(symbol)
        return float(ticker["last"])
    except Exception as e:
        print(f"[get_live_price] {symbol}: {e}")
        return None

def _is_valid(df, min_rows=60):
    return (df is not None) and (not df.empty) and (len(df) >= min_rows)

# ── INDICATORS
def ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def rsi(series, period=14):
    delta = series.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss.replace(0, 1e-9)
    return 100 - (100 / (1 + rs))

def macd(series):
    fast   = ema(series, 12)
    slow   = ema(series, 26)
    line   = fast - slow
    signal = ema(line, 9)
    hist   = line - signal
    return line, signal, hist

def bollinger(series, period=20):
    mid = series.rolling(period).mean()
    std = series.rolling(period).std()
    return (mid + 2 * std), mid, (mid - 2 * std)

def atr(df, period=14):
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift()).abs()
    lc = (df["low"]  - df["close"].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()

def stochastic(df, k=14, d=3):
    lo   = df["low"].rolling(k).min()
    hi   = df["high"].rolling(k).max()
    k_ln = 100 * (df["close"] - lo) / (hi - lo + 1e-10)
    d_ln = k_ln.rolling(d).mean()
    return k_ln, d_ln

def vwap(df):
    tp      = (df["high"] + df["low"] + df["close"]) / 3
    cum_vol = df["volume"].cumsum().replace(0, 1e-10)
    return (tp * df["volume"]).cumsum() / cum_vol

def adx(df, period=14):
    """
    Average Directional Index.
    Returns (adx_val, plus_di, minus_di).
    ADX > 25 = trending market, trade with trend.
    ADX < 20 = sideways/choppy, skip directional trades.
    """
    try:
        high  = df["high"].values.astype(float)
        low   = df["low"].values.astype(float)
        close = df["close"].values.astype(float)
        n     = len(close)

        plus_dm  = np.zeros(n)
        minus_dm = np.zeros(n)
        tr_arr   = np.zeros(n)

        for i in range(1, n):
            up   = high[i]  - high[i-1]
            down = low[i-1] - low[i]
            plus_dm[i]  = up   if (up > down and up > 0)   else 0.0
            minus_dm[i] = down if (down > up and down > 0) else 0.0
            tr_arr[i]   = max(high[i]-low[i],
                              abs(high[i]-close[i-1]),
                              abs(low[i]-close[i-1]))

        # Wilder's smoothing
        def wilder(arr, p):
            out = np.zeros(len(arr))
            out[p] = np.sum(arr[1:p+1])
            for i in range(p+1, len(arr)):
                out[i] = out[i-1] - out[i-1]/p + arr[i]
            return out

        tr_sm   = wilder(tr_arr,   period)
        pdm_sm  = wilder(plus_dm,  period)
        mdm_sm  = wilder(minus_dm, period)

        with np.errstate(divide='ignore', invalid='ignore'):
            pdi = np.where(tr_sm > 0, 100 * pdm_sm / tr_sm, 0.0)
            mdi = np.where(tr_sm > 0, 100 * mdm_sm / tr_sm, 0.0)
            dx  = np.where((pdi + mdi) > 0,
                           100 * np.abs(pdi - mdi) / (pdi + mdi), 0.0)

        adx_arr = wilder(dx, period)

        return (
            float(adx_arr[-1]),
            float(pdi[-1]),
            float(mdi[-1]),
        )
    except Exception:
        return 25.0, 25.0, 25.0   # neutral fallback

def ichimoku_bias(df15) -> str:
    """
    Quick Ichimoku cloud bias from 15m data.
    Returns "BULL", "BEAR", or "NEUTRAL".
    Uses Tenkan (9), Kijun (26), and Senkou Span A/B comparison.
    """
    try:
        if df15 is None or len(df15) < 52:
            return "NEUTRAL"
        high = df15["high"]
        low  = df15["low"]
        close = df15["close"]

        tenkan = (high.rolling(9).max()  + low.rolling(9).min())  / 2
        kijun  = (high.rolling(26).max() + low.rolling(26).min()) / 2
        ssa    = ((tenkan + kijun) / 2).shift(26)
        ssb    = ((high.rolling(52).max() + low.rolling(52).min()) / 2).shift(26)

        price   = float(close.iloc[-1])
        ssa_val = float(ssa.iloc[-1]) if not pd.isna(ssa.iloc[-1]) else price
        ssb_val = float(ssb.iloc[-1]) if not pd.isna(ssb.iloc[-1]) else price
        cloud_top    = max(ssa_val, ssb_val)
        cloud_bottom = min(ssa_val, ssb_val)

        tk_val = float(tenkan.iloc[-1])
        kj_val = float(kijun.iloc[-1])

        bull_pts = 0
        bear_pts = 0

        if price > cloud_top:   bull_pts += 2
        elif price < cloud_bottom: bear_pts += 2

        if tk_val > kj_val: bull_pts += 1
        else:               bear_pts += 1

        if ssa_val > ssb_val: bull_pts += 1
        else:                 bear_pts += 1

        if   bull_pts >= 3: return "BULL"
        elif bear_pts >= 3: return "BEAR"
        return "NEUTRAL"
    except Exception:
        return "NEUTRAL"

def detect_rsi_divergence(df, rsi_series, lookback=20) -> str:
    """
    Detects RSI divergence patterns over last `lookback` candles.
    Bullish divergence: price makes lower low, RSI makes higher low → reversal up
    Bearish divergence: price makes higher high, RSI makes lower high → reversal down
    Returns: "BULL_DIV", "BEAR_DIV", or ""
    """
    try:
        if df is None or len(df) < lookback + 5:
            return ""

        closes = df["close"].values[-lookback:]
        rsi_v  = rsi_series.values[-lookback:]

        # Find swing lows for bullish divergence
        price_lows = []
        rsi_lows   = []
        for i in range(2, len(closes)-2):
            if closes[i] <= closes[i-1] and closes[i] <= closes[i-2] \
               and closes[i] <= closes[i+1] and closes[i] <= closes[i+2]:
                price_lows.append((i, closes[i]))
                rsi_lows.append((i, rsi_v[i]))

        if len(price_lows) >= 2:
            p1, p2 = price_lows[-2], price_lows[-1]
            r1, r2 = rsi_lows[-2],   rsi_lows[-1]
            if p2[1] < p1[1] and r2[1] > r1[1]:   # price lower low, RSI higher low
                return "BULL_DIV"

        # Find swing highs for bearish divergence
        price_highs = []
        rsi_highs   = []
        for i in range(2, len(closes)-2):
            if closes[i] >= closes[i-1] and closes[i] >= closes[i-2] \
               and closes[i] >= closes[i+1] and closes[i] >= closes[i+2]:
                price_highs.append((i, closes[i]))
                rsi_highs.append((i, rsi_v[i]))

        if len(price_highs) >= 2:
            p1, p2 = price_highs[-2], price_highs[-1]
            r1, r2 = rsi_highs[-2],   rsi_highs[-1]
            if p2[1] > p1[1] and r2[1] < r1[1]:   # price higher high, RSI lower high
                return "BEAR_DIV"

        return ""
    except Exception:
        return ""

# ── TREND AND VOLATILITY FILTERS
def trend_direction(df):
    e20      = float(ema(df["close"], 20).iloc[-1])
    e50      = float(ema(df["close"], 50).iloc[-1])
    e50_prev = float(ema(df["close"], 50).iloc[-5])
    slope    = (e50 - e50_prev) / (e50_prev + 1e-10) * 100
    if e20 > e50 and slope > 0.02:  return "BULL"
    if e20 < e50 and slope < -0.02: return "BEAR"
    return "SIDEWAYS"

def trend_strength(df):
    if len(df) < 20: return 20.0
    returns     = df["close"].pct_change().dropna()
    pos         = int((returns > 0).sum())
    neg         = int((returns < 0).sum())
    total       = pos + neg
    if total == 0: return 0.0
    consistency = abs(pos - neg) / total * 100
    magnitude   = float(returns.abs().mean()) * 1000
    return round(min(100.0, consistency * 0.5 + magnitude * 0.5), 1)

def is_sideways(df):
    if not config.AVOID_SIDEWAYS: return False
    price   = float(df["close"].iloc[-1])
    std_val = float(df["close"].rolling(20).std().iloc[-1])
    if np.isnan(std_val): return False
    bb_width = (4 * std_val) / (price + 1e-10) * 100
    return bb_width < 0.45

def analyze_volume(df):
    vols    = df["volume"]
    avg_raw = vols.rolling(20).mean().iloc[-1]
    if pd.isna(avg_raw) or avg_raw == 0:
        return False, False, "FLAT ➡️"
    avg     = float(avg_raw)
    current = float(vols.iloc[-1])
    vol_ok    = current > avg * config.VOLUME_MULT
    vol_spike = current > avg * config.SPIKE_VOLUME_MULT
    last = vols.iloc[-5:].values
    if len(last) >= 5:
        slope = float(np.polyfit(range(5), last, 1)[0])
        if slope >  avg * 0.02: vol_trend = "RISING 📈"
        elif slope < -avg * 0.02: vol_trend = "FALLING 📉"
        else: vol_trend = "FLAT ➡️"
    else:
        vol_trend = "FLAT ➡️"
    return vol_ok, vol_spike, vol_trend

def detect_entry_trigger(df, action, ema21_val):
    closes = df["close"].values
    highs  = df["high"].values
    lows   = df["low"].values
    opens  = df["open"].values
    price  = float(closes[-1])
    n      = 15

    if len(closes) >= n:
        if action == "BUY"  and price > float(max(highs[-n:-1])): return "BREAKOUT 🚀"
        if action == "SELL" and price < float(min(lows[-n:-1])):  return "BREAKOUT 🚀"

    ema21 = float(ema21_val)
    near_ema = abs(price - ema21) / (ema21 + 1e-10) < 0.0025

    if len(closes) >= 3:
        if action == "BUY"  and float(closes[-3]) < ema21 and price >= ema21: return "SR_BOUNCE 🔄"
        if action == "SELL" and float(closes[-3]) > ema21 and price <= ema21: return "SR_BOUNCE 🔄"

    if near_ema: return "PULLBACK 🎯"

    if len(closes) >= 4:
        bodies = [abs(float(closes[-i]) - float(opens[-i])) for i in range(1, 4)]
        if action == "BUY"  and all(float(closes[-i]) > float(opens[-i]) for i in range(1,4)):
            if bodies[0] >= bodies[1]: return "MOMENTUM ⚡"
        if action == "SELL" and all(float(closes[-i]) < float(opens[-i]) for i in range(1,4)):
            if bodies[0] >= bodies[1]: return "MOMENTUM ⚡"

    return "SIGNAL 📊"

def detect_pattern(df):
    if len(df) < 3: return ""
    o = df["open"].values;  h = df["high"].values
    l = df["low"].values;   c = df["close"].values
    body = abs(float(c[-1]) - float(o[-1]))
    rng  = float(h[-1]) - float(l[-1]) + 1e-10
    lw   = min(float(o[-1]), float(c[-1])) - float(l[-1])
    uw   = float(h[-1]) - max(float(o[-1]), float(c[-1]))
    if body / rng < 0.1:                                                        return "DOJI"
    if lw > 2*body and uw < body and float(c[-1]) > float(o[-1]):              return "HAMMER"
    if uw > 2*body and lw < body and float(c[-1]) < float(o[-1]):              return "SHOOTING_STAR"
    if (float(c[-2])<float(o[-2]) and float(c[-1])>float(o[-1])
            and float(o[-1])<float(c[-2]) and float(c[-1])>float(o[-2])):      return "BULL_ENGULF"
    if (float(c[-2])>float(o[-2]) and float(c[-1])<float(o[-1])
            and float(o[-1])>float(c[-2]) and float(c[-1])<float(o[-2])):      return "BEAR_ENGULF"
    return ""

def mtf_confirm(df1m, df3m, df5m, action):
    if not config.MTF_CONFIRM_ON: return True
    try:
        e9_1m  = float(ema(df1m["close"],  9).iloc[-1])
        e21_1m = float(ema(df1m["close"], 21).iloc[-1])
        rsi_3m = float(rsi(df3m["close"]).iloc[-1])
        e9_series = ema(df1m["close"], 9)
        e9_slope  = float(e9_series.iloc[-1]) - float(e9_series.iloc[-3])
        _, _, hist_5m = macd(df5m["close"])
        h5 = float(hist_5m.iloc[-1])

        if action == "BUY":
            votes = [
                e9_1m > e21_1m,
                rsi_3m > 48,
                h5 > 0,
                e9_slope > 0,
            ]
        else:
            votes = [
                e9_1m < e21_1m,
                rsi_3m < 52,
                h5 < 0,
                e9_slope < 0,
            ]

        passed = sum(votes)
        if passed < 3:
            print(f"[mtf_confirm] failed {passed}/4 votes for {action}")
            return False
        return True

    except Exception as e:
        print(f"[mtf_confirm] skipped: {e}")
        return True

def calc_tp_sl(action, entry, atr_val):
    entry   = float(entry)
    atr_val = float(atr_val)

    if action == "BUY":
        sl  = entry - atr_val * config.ATR_SL
        tp1 = entry + atr_val * config.ATR_TP1
        tp2 = entry + atr_val * config.ATR_TP2
        tp3 = entry + atr_val * config.ATR_TP3
    else:
        sl  = entry + atr_val * config.ATR_SL
        tp1 = entry - atr_val * config.ATR_TP1
        tp2 = entry - atr_val * config.ATR_TP2
        tp3 = entry - atr_val * config.ATR_TP3

    sl_pct = abs(sl - entry) / (entry + 1e-10) * 100
    if sl_pct > config.MAX_SL_PCT:
        if action == "BUY":
            sl = entry * (1 - config.MAX_SL_PCT / 100)
        else:
            sl = entry * (1 + config.MAX_SL_PCT / 100)
        sl_pct = config.MAX_SL_PCT

    tp1_pct = abs(tp1 - entry) / (entry + 1e-10) * 100
    rr      = tp1_pct / (sl_pct + 1e-10)

    return tp1, tp2, tp3, sl, round(sl_pct, 3), round(rr, 2), round(atr_val, 6)

def calculate_confidence(bull, bear):
    score = abs(int(bull) - int(bear))
    conf  = 50 + int((score / 19) * 45)
    return min(95, conf)

# ── MAIN SIGNAL FUNCTION
def simple_signal(df3, df5, df15, df1=None, active_symbol="BTC/USDT"):
    if not _is_valid(df3,  min_rows=60): return None
    if not _is_valid(df5,  min_rows=60): return None
    if not _is_valid(df15, min_rows=60): return None

    if df1 is None or not _is_valid(df1, min_rows=10):
        df1 = df3

    closes3 = df3["close"]
    price   = float(closes3.iloc[-2])

    try:
        from realtime_price import get_price
        live_price = get_price(active_symbol, exchange)
        if live_price is not None:
            live_price = float(live_price)
            if abs(live_price - price) / (price + 1e-10) * 100 < 0.15:
                price = live_price
    except Exception:
        pass

    # ── TREND CHECK
    trend    = trend_direction(df15)
    ts       = float(trend_strength(df15))
    sideways = bool(is_sideways(df3))
    if sideways or ts < config.MIN_TREND_STRENGTH:
        return None

    # ── ADX GATE: Skip directionless markets (ADX < 18 = no trend)
    adx_val, pdi, mdi = adx(df3, period=14)
    adx_trending = adx_val >= 18.0
    if not adx_trending:
        print(f"[strategy] ADX={adx_val:.1f} < 18 — choppy market, no entry")
        return None

    # ── ATR % GATE: Skip dead/micro-volatility markets
    atr_series  = atr(df3)
    atr_val_raw = float(atr_series.iloc[-1])
    if np.isnan(atr_val_raw) or atr_val_raw == 0:
        return None
    atr_pct = atr_val_raw / price * 100
    if atr_pct < 0.05:   # less than 0.05% ATR = completely dead market
        print(f"[strategy] ATR%={atr_pct:.4f}% < 0.05% — dead market, skip")
        return None

    # ── VOLUME GATE
    vol_ok, vol_spike, vol_trend = analyze_volume(df3)
    vol_ok    = bool(vol_ok)
    vol_spike = bool(vol_spike)
    if not vol_ok:
        return None

    # ── INDICATORS
    rsi_series     = rsi(closes3)
    rsi_val        = float(rsi_series.iloc[-1])
    ml, ms, mhist  = macd(df5["close"])
    macd_h         = float(mhist.iloc[-1])
    macd_l         = float(ml.iloc[-1])
    macd_s         = float(ms.iloc[-1])
    bb_u, bb_m, bb_l = bollinger(closes3)
    bb_upper       = float(bb_u.iloc[-1])
    bb_mid         = float(bb_m.iloc[-1])
    bb_lower       = float(bb_l.iloc[-1])
    ema9_val       = float(ema(closes3,  9).iloc[-1])
    ema21_val      = float(ema(closes3, 21).iloc[-1])
    ema50_val      = float(ema(closes3, 50).iloc[-1])

    sk, sd         = stochastic(df3)
    stoch_k        = float(sk.iloc[-1])
    stoch_d        = float(sd.iloc[-1])

    vwap_series    = vwap(df3)
    vwap_val       = float(vwap_series.iloc[-1])
    pattern        = detect_pattern(df3)

    # ── RSI DIVERGENCE (bonus signal, added to score)
    rsi_div = detect_rsi_divergence(df3, rsi_series, lookback=20)

    # ── ICHIMOKU BIAS (15m confirmation)
    ichi_bias = ichimoku_bias(df15)

    # ── SCORING ENGINE
    bull = 0
    bear = 0

    if trend == "BULL":   bull += 3
    elif trend == "BEAR": bear += 3

    # ADX directional bias: +DI > -DI = bullish momentum
    if pdi > mdi + 5:  bull += 1
    elif mdi > pdi + 5: bear += 1

    if rsi_val < 30:    bull += 3
    elif rsi_val < 40:  bull += 2
    elif rsi_val < 48:  bull += 1
    if rsi_val > 70:    bear += 3
    elif rsi_val > 60:  bear += 2
    elif rsi_val > 52:  bear += 1

    if macd_h > 0 and macd_l > macd_s:  bull += 2
    elif macd_h > 0:                      bull += 1
    if macd_h < 0 and macd_l < macd_s:  bear += 2
    elif macd_h < 0:                      bear += 1

    if price <= bb_lower:  bull += 3
    elif price <= bb_mid:  bull += 1
    if price >= bb_upper:  bear += 3
    elif price >= bb_mid:  bear += 1

    if ema9_val > ema21_val: bull += 2
    else:                     bear += 2

    if price > ema50_val: bull += 1
    else:                  bear += 1

    if stoch_k < 20 and stoch_k > stoch_d:  bull += 2
    elif stoch_k < 35:                       bull += 1
    if stoch_k > 80 and stoch_k < stoch_d:  bear += 2
    elif stoch_k > 65:                       bear += 1

    if price > vwap_val: bull += 2
    else:                 bear += 2

    if vol_spike:
        if bull > bear: bull += 1
        else:            bear += 1
    elif vol_ok:
        if bull > bear: bull += 1
        else:            bear += 1

    if pattern in {"HAMMER", "BULL_ENGULF"}:         bull += 1
    if pattern in {"SHOOTING_STAR", "BEAR_ENGULF"}:  bear += 1

    # RSI divergence bonus
    if rsi_div == "BULL_DIV": bull += 2
    if rsi_div == "BEAR_DIV": bear += 2

    # Ichimoku alignment bonus
    if ichi_bias == "BULL": bull += 1
    elif ichi_bias == "BEAR": bear += 1

    score = bull - bear

    if abs(score) < config.MIN_BULL_BEAR_SCORE:
        return None

    action = "BUY" if score > 0 else "SELL"

    if action == "BUY"  and trend == "BEAR": return None
    if action == "SELL" and trend == "BULL": return None

    confidence = calculate_confidence(bull, bear)
    if confidence < config.MIN_CONFIDENCE:
        return None

    tp1, tp2, tp3, sl, sl_pct, rr, atr_v = calc_tp_sl(action, price, atr_val_raw)

    if rr < config.MIN_RR_RATIO:
        return None

    mtf_ok = bool(mtf_confirm(df1, df3, df5, action))
    if not mtf_ok:
        return None

    trigger = detect_entry_trigger(df3, action, ema21_val)

    if vol_spike:
        regime = "BREAKOUT 🚀"
    elif ts > 25:
        regime = "TRENDING 📈" if action == "BUY" else "TRENDING 📉"
    else:
        regime = "WEAK TREND ⚠️"

    return {
        "signal":         action,
        "confidence":     confidence,
        "score":          score,
        "bull":           bull,
        "bear":           bear,
        "atr":            round(atr_v, 6),
        "atr_pct":        round(atr_pct, 3),
        "tp1":            tp1,
        "tp2":            tp2,
        "tp3":            tp3,
        "sl":             sl,
        "sl_pct":         sl_pct,
        "rr":             rr,
        "rsi":            round(rsi_val, 1),
        "macd_hist":      round(macd_h, 6),
        "ema9":           round(ema9_val, 4),
        "ema21":          round(ema21_val, 4),
        "vwap":           round(vwap_val, 4),
        "bb_upper":       round(bb_upper, 4),
        "bb_lower":       round(bb_lower, 4),
        # NEW — now included for signal_quality.py dim-6
        "stoch_k":        round(stoch_k, 1),
        "stoch_d":        round(stoch_d, 1),
        "price":          round(price, 8),
        # NEW
        "adx":            round(adx_val, 1),
        "adx_pdi":        round(pdi, 1),
        "adx_mdi":        round(mdi, 1),
        "rsi_divergence": rsi_div,
        "ichimoku_bias":  ichi_bias,
        # Existing
        "trend":          trend,
        "trend_strength": ts,
        "vol_spike":      vol_spike,
        "vol_trend":      vol_trend,
        "pattern":        pattern,
        "market_regime":  regime,
        "entry_trigger":  trigger,
        "mtf_ok":         mtf_ok,
    }
