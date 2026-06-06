# ╔══════════════════════════════════════════════════════════════╗
# ║  trade_filter.py  —  Pre-Entry Quality Guard v1.0            ║
# ║                                                              ║
# ║  PURPOSE: Block entries that are statistically likely to     ║
# ║  hit their SL before reaching TP1. Called in main.py just   ║
# ║  before create_trade(). All checks return (ok, reason).     ║
# ║                                                              ║
# ║  Checks:                                                     ║
# ║  1. Funding rate gate  — high funding = reversal risk        ║
# ║  2. Spread gate        — wide spread eats R:R                ║
# ║  3. Wick ratio filter  — recent candles have huge wicks      ║
# ║  4. S/R proximity      — entering right into a wall          ║
# ║  5. Candle close confirm — signal candle must be closed      ║
# ╚══════════════════════════════════════════════════════════════╝

import numpy as np
import config


# ══════════════════════════════════════════════════════════════════
# 1. FUNDING RATE GATE
# ══════════════════════════════════════════════════════════════════
def check_funding_rate(exchange, coin: str, signal: str) -> tuple[bool, str]:
    """
    High positive funding = longs are over-leveraged → BUY entries risky.
    High negative funding = shorts are over-leveraged → SELL entries risky.
    Funding rate is in % per 8h. Max allowed: config.MAX_FUNDING_RATE_PCT.
    """
    try:
        market_id = coin.replace("/", "").replace(":USDT", "") + "USDT"
        # Binance futures funding endpoint
        result = exchange.fapiPublicGetPremiumIndex({"symbol": market_id})
        if isinstance(result, list):
            result = result[0]
        rate_str = result.get("lastFundingRate", "0")
        rate_pct = float(rate_str) * 100   # convert from decimal to %

        max_rate = getattr(config, "MAX_FUNDING_RATE_PCT", 0.05)

        if signal == "BUY" and rate_pct > max_rate:
            return False, f"HIGH_FUNDING_LONG: {rate_pct:.4f}% (longs over-leveraged, reversal risk)"
        if signal == "SELL" and rate_pct < -max_rate:
            return False, f"HIGH_FUNDING_SHORT: {rate_pct:.4f}% (shorts over-leveraged, squeeze risk)"

        return True, f"funding_ok:{rate_pct:.4f}%"

    except Exception as e:
        # Fallback: don't block on fetch error
        print(f"[funding_gate] {coin}: {e}")
        return True, "funding_fetch_failed:skip"


# ══════════════════════════════════════════════════════════════════
# 2. SPREAD GATE
# ══════════════════════════════════════════════════════════════════
def check_spread(exchange, coin: str) -> tuple[bool, str]:
    """
    Wide bid-ask spread eats into R:R before the trade even moves.
    Max allowed spread: config.MAX_SPREAD_PCT (default 0.04%).
    """
    try:
        ob = exchange.fetch_order_book(coin, limit=1)
        best_bid = float(ob["bids"][0][0]) if ob.get("bids") else None
        best_ask = float(ob["asks"][0][0]) if ob.get("asks") else None

        if best_bid is None or best_ask is None or best_bid <= 0:
            return True, "spread_fetch_failed:skip"

        mid       = (best_bid + best_ask) / 2
        spread_pct = (best_ask - best_bid) / mid * 100
        max_spread = getattr(config, "MAX_SPREAD_PCT", 0.04)

        if spread_pct > max_spread:
            return False, f"WIDE_SPREAD: {spread_pct:.4f}% > {max_spread}%"

        return True, f"spread_ok:{spread_pct:.4f}%"

    except Exception as e:
        print(f"[spread_gate] {coin}: {e}")
        return True, "spread_fetch_failed:skip"


# ══════════════════════════════════════════════════════════════════
# 3. WICK RATIO FILTER
# ══════════════════════════════════════════════════════════════════
def check_wick_ratio(df, signal: str) -> tuple[bool, str]:
    """
    If recent candles have huge wicks in the signal direction, that means
    the market is aggressively rejecting moves that way — entering into it
    means our SL will get hit by the next rejection wick.

    Wick ratio = upper/lower wick vs body size.
    Block if avg directional wick > 2.5× body over last 5 candles.
    """
    try:
        if df is None or len(df) < 6:
            return True, "wick_check_skip:insufficient_data"

        tail = df.iloc[-6:-1]   # last 5 completed candles
        bodies  = (tail["close"] - tail["open"]).abs()
        avg_body = float(bodies.mean())

        if avg_body == 0:
            return True, "wick_check_skip:zero_body"

        if signal == "BUY":
            # Upper wick = high - max(open, close). Wicks above = rejection of upward moves.
            upper_wicks = tail["high"] - tail[["open", "close"]].max(axis=1)
            avg_wick = float(upper_wicks.mean())
        else:
            # Lower wick = min(open, close) - low. Wicks below = rejection of downward moves.
            lower_wicks = tail[["open", "close"]].min(axis=1) - tail["low"]
            avg_wick = float(lower_wicks.mean())

        wick_ratio = avg_wick / (avg_body + 1e-10)

        if wick_ratio > 2.5:
            return False, f"WICK_REJECTION: ratio={wick_ratio:.2f} (market rejecting {signal} moves)"

        return True, f"wick_ok:{wick_ratio:.2f}"

    except Exception as e:
        print(f"[wick_gate] {e}")
        return True, "wick_check_failed:skip"


# ══════════════════════════════════════════════════════════════════
# 4. SUPPORT / RESISTANCE PROXIMITY
# ══════════════════════════════════════════════════════════════════
def _find_sr_levels(df, lookback: int = 50) -> list[float]:
    """
    Finds significant S/R levels as swing highs and swing lows
    in the last `lookback` candles. A swing high is a candle whose
    high is the highest in a ±3 candle window.
    """
    levels = []
    if df is None or len(df) < lookback + 3:
        return levels

    data = df.iloc[-lookback:]
    highs = data["high"].values
    lows  = data["low"].values

    for i in range(3, len(data) - 3):
        # Swing high
        if highs[i] == max(highs[i-3:i+4]):
            levels.append(float(highs[i]))
        # Swing low
        if lows[i] == min(lows[i-3:i+4]):
            levels.append(float(lows[i]))

    return levels


def check_sr_proximity(df, price: float, signal: str, atr_val: float) -> tuple[bool, str]:
    """
    Block entry if price is within 0.3% of a strong S/R level in the
    direction of the trade (a wall right in front of TP1).
    Also block entry if price just bounced off S/R against the signal.
    """
    try:
        levels = _find_sr_levels(df)
        if not levels:
            return True, "sr_check_skip:no_levels"

        proximity_pct = 0.30   # % distance to consider "at the wall"

        for level in levels:
            dist_pct = abs(price - level) / (price + 1e-10) * 100

            if dist_pct < proximity_pct:
                # Price is at an S/R level — is it acting as resistance?
                if signal == "BUY" and price < level:
                    return False, f"SR_RESISTANCE: {level:.4f} is {dist_pct:.3f}% above — wall blocking BUY"
                if signal == "SELL" and price > level:
                    return False, f"SR_SUPPORT: {level:.4f} is {dist_pct:.3f}% below — wall blocking SELL"

        return True, "sr_ok"

    except Exception as e:
        print(f"[sr_gate] {e}")
        return True, "sr_check_failed:skip"


# ══════════════════════════════════════════════════════════════════
# 5. CANDLE CLOSE CONFIRMATION
# ══════════════════════════════════════════════════════════════════
def check_candle_close(df, signal: str) -> tuple[bool, str]:
    """
    The last completed candle (iloc[-2], since iloc[-1] is the live candle)
    must close convincingly in the signal direction.

    A convincing close means:
    - BUY: close is in the top 45% of the candle range (close_position > 0.55)
    - SELL: close is in the bottom 45% of the candle range (close_position < 0.45)
    - Body must be at least 30% of the total candle range (not a doji)

    This prevents entries after a candle that spiked up but closed weak.
    """
    try:
        if df is None or len(df) < 3:
            return True, "close_check_skip:insufficient_data"

        # Use the last COMPLETED candle (iloc[-2])
        c = df.iloc[-2]
        o, h, l, cl = float(c["open"]), float(c["high"]), float(c["low"]), float(c["close"])

        candle_range = h - l
        if candle_range < 1e-10:
            return True, "close_check_skip:zero_range"

        body_size   = abs(cl - o)
        body_ratio  = body_size / candle_range

        if body_ratio < 0.30:   # doji / spinning top — indecision
            return False, f"DOJI_CANDLE: body_ratio={body_ratio:.2f} (indecision, not directional)"

        close_position = (cl - l) / candle_range   # 0 = at low, 1 = at high

        if signal == "BUY" and close_position < 0.55:
            return False, f"WEAK_BULL_CLOSE: close at {close_position:.0%} of range (need top 45%, >0.55)"
        if signal == "SELL" and close_position > 0.45:
            return False, f"WEAK_BEAR_CLOSE: close at {close_position:.0%} of range (need bottom 45%, <0.45)"

        return True, f"close_ok:pos={close_position:.2f}"

    except Exception as e:
        print(f"[close_gate] {e}")
        return True, "close_check_failed:skip"


# ══════════════════════════════════════════════════════════════════
# MASTER FILTER — called from main.py before create_trade()
# ══════════════════════════════════════════════════════════════════
def run_pre_entry_checks(
    coin: str,
    signal: str,
    price: float,
    atr_val: float,
    df3,            # 3m candle dataframe
    exchange,
) -> tuple[bool, str]:
    """
    Runs all pre-entry quality checks in order of cheapest → most expensive.
    Returns (True, "ok") if all pass, or (False, reason) on first block.

    Integration in main.py:
        from trade_filter import run_pre_entry_checks
        ok, reason = run_pre_entry_checks(coin, sig["signal"], price, sig["atr"], df3, exchange)
        if not ok:
            print(f"[pre-entry-block] {coin}: {reason}")
            return
    """

    # 1. Candle close (cheapest — just reads df)
    ok, reason = check_candle_close(df3, signal)
    if not ok:
        return False, f"CANDLE_CLOSE | {reason}"

    # 2. Wick ratio (cheap — just reads df)
    ok, reason = check_wick_ratio(df3, signal)
    if not ok:
        return False, f"WICK_RATIO | {reason}"

    # 3. S/R proximity (moderate — reads df, some computation)
    ok, reason = check_sr_proximity(df3, price, signal, atr_val)
    if not ok:
        return False, f"SR_PROXIMITY | {reason}"

    # 4. Spread check (1 API call)
    ok, reason = check_spread(exchange, coin)
    if not ok:
        return False, f"SPREAD | {reason}"

    # 5. Funding rate (1 API call — most expensive, do last)
    ok, reason = check_funding_rate(exchange, coin, signal)
    if not ok:
        return False, f"FUNDING | {reason}"

    return True, "ok"
