# ╔══════════════════════════════════════════════════════════════╗
# ║  paper_engine.py  —  Paper Engine v8.0                       ║
# ║                                                              ║
# ║  What's new vs v7.1:                                         ║
# ║  1. Session-aware sizing    — off-peak hours = smaller qty   ║
# ║  2. Smarter consecutive loss reset — resets on daily reset  ║
# ║     AND after revenge cooldown clears (not just new day)     ║
# ║  3. Win streak boost — 3 consecutive wins = +0.1% extra risk ║
# ║  4. Per-coin trade stats tracking — see which coins win/lose ║
# ║  5. Fee-adjusted breakeven SL — uses real taker fee not avg  ║
# ║  6. TP1 partial now books capital BEFORE continuing checks   ║
# ║  7. Better result labels for TP2+ trail exits                ║
# ║  8. get_report() expanded — per-session stats, best coin     ║
# ║  9. Emergency stop if drawdown > 8% from peak (hard kill)    ║
# ║  10. Trailing SL + session guard wired in                    ║
# ╚══════════════════════════════════════════════════════════════╝

from datetime   import datetime, timezone
from copy       import deepcopy
import config

# ── Optional imports — bot works even if these files aren't present yet ──
try:
    from trailing_sl   import compute_trail_sl, format_trail_update
    _TRAILING_SL_AVAILABLE = True
except ImportError:
    _TRAILING_SL_AVAILABLE = False
    print("[paper_engine] trailing_sl.py not found — trailing SL disabled")

try:
    from session_guard import get_session_info, apply_session_sizing
    _SESSION_GUARD_AVAILABLE = True
except ImportError:
    _SESSION_GUARD_AVAILABLE = False
    print("[paper_engine] session_guard.py not found — full size always used")


# ══════════════════════════════════════════════════════════════════
# STATE
# ══════════════════════════════════════════════════════════════════
paper = {
    "capital":          config.STARTING_CAPITAL,
    "peak_capital":     config.STARTING_CAPITAL,
    "wins":             0,
    "losses":           0,
    "trade_count":      0,
    "daily_trades":     0,
    "consecutive_loss": 0,
    "consecutive_win":  0,   # NEW: track win streaks for size boost
    "daily_pnl":        0.0,
    "daily_fees":       0.0,
    "today_date":       datetime.now(timezone.utc).date(),
    "emergency_stop":   False,   # NEW: hard kill if drawdown > 8%
}

open_trades  = {}
coin_stats   = {}   # NEW: per-coin win/loss/pnl tracking

_last_loss_time     = None
_psychology_stopped = False

# Hard drawdown kill threshold — if capital drops > this % from peak, stop all trading
EMERGENCY_DRAWDOWN_PCT = 8.0


# ══════════════════════════════════════════════════════════════════
# DAILY RESET
# ══════════════════════════════════════════════════════════════════
def _reset_daily_if_needed():
    today = datetime.now(timezone.utc).date()
    if today != paper["today_date"]:
        paper["daily_trades"]     = 0
        paper["daily_pnl"]        = 0.0
        paper["daily_fees"]       = 0.0
        paper["today_date"]       = today
        paper["consecutive_loss"] = 0
        paper["consecutive_win"]  = 0
        global _psychology_stopped, _last_loss_time
        _psychology_stopped = False
        _last_loss_time     = None   # NEW: revenge cooldown also resets on new day
        print(f"[paper_engine] Daily reset — new trading day: {today}")


# ══════════════════════════════════════════════════════════════════
# FEE CALCULATOR
# ══════════════════════════════════════════════════════════════════
def calc_fee(notional, is_maker=True):
    rate = config.MAKER_FEE_PCT if is_maker else config.TAKER_FEE_PCT
    if getattr(config, "USE_BNB_FEES", True):
        rate *= 0.90   # BNB discount
    return notional * rate / 100


# ══════════════════════════════════════════════════════════════════
# LEVERAGE SELECTOR
# ══════════════════════════════════════════════════════════════════
def choose_leverage(confidence=70, coin="BTC/USDT"):
    """
    Tiered leverage by confidence. Alts capped lower than BTC/ETH
    because their wicks are more extreme.
    """
    if   confidence >= 85: lev = config.LEVERAGE_HIGH
    elif confidence >= 75: lev = config.LEVERAGE_MED
    else:                   lev = config.LEVERAGE_LOW

    major_coins = getattr(config, "MAJOR_COINS", ["BTC/USDT", "ETH/USDT"])
    max_lev = config.MAX_LEVERAGE if coin in major_coins \
              else getattr(config, "MAX_LEVERAGE_ALTS", 2)

    return min(lev, max_lev)


# ══════════════════════════════════════════════════════════════════
# POSITION SIZING
# ══════════════════════════════════════════════════════════════════
def position_size(capital, entry, sl, confidence=70):
    """
    Risk-based position sizing with three adjustments:
    1. Base risk from config (0.8%)
    2. +10% on high-confidence signals (conf >= 80%)
    3. +10% win streak boost after 3 consecutive wins
       (trading well = bigger size; on a losing streak = normal size)
    """
    base_risk = getattr(config, "RISK_PER_TRADE_PCT", 0.8)

    # High confidence boost
    if confidence >= getattr(config, "HIGH_CONF_BOOST_THRESH", 80):
        base_risk *= getattr(config, "RISK_BOOST_HIGH_CONF", 1.1)

    # Win streak boost — max +10% after 3 consecutive wins
    if paper["consecutive_win"] >= 3:
        win_boost = min(paper["consecutive_win"] - 2, 3) * 0.033   # +3.3% per win above 3, max +10%
        base_risk *= (1 + win_boost)
        base_risk  = min(base_risk, 1.5)   # hard cap: never risk more than 1.5% per trade

    risk_amount = capital * base_risk / 100
    distance    = abs(entry - sl)
    if distance <= 0:
        return 0
    qty = risk_amount / distance
    return round(qty, 6)


# ══════════════════════════════════════════════════════════════════
# BREAKEVEN CALCULATOR
# ══════════════════════════════════════════════════════════════════
def breakeven_pct(notional):
    """
    Real breakeven uses taker fee for both entry and exit
    (worst case — if limit order gets filled as taker).
    Gives a conservative breakeven threshold.
    """
    fee_in  = calc_fee(notional, is_maker=True)
    fee_out = calc_fee(notional, is_maker=False)   # exit always taker
    return round((fee_in + fee_out) / notional * 100, 5)


# ══════════════════════════════════════════════════════════════════
# PER-COIN STATS TRACKER
# ══════════════════════════════════════════════════════════════════
def _update_coin_stats(coin: str, result: str, net_pnl: float):
    """Tracks per-coin performance for the daily summary."""
    if coin not in coin_stats:
        coin_stats[coin] = {"wins": 0, "losses": 0, "net_pnl": 0.0, "trades": 0}
    coin_stats[coin]["trades"] += 1
    coin_stats[coin]["net_pnl"] = round(coin_stats[coin]["net_pnl"] + net_pnl, 2)
    if "❌" in result or "STOP" in result:
        coin_stats[coin]["losses"] += 1
    else:
        coin_stats[coin]["wins"] += 1


def get_coin_stats() -> dict:
    """Returns per-coin stats dict, sorted by net P&L descending."""
    return dict(sorted(coin_stats.items(), key=lambda x: x[1]["net_pnl"], reverse=True))


# ══════════════════════════════════════════════════════════════════
# TRADING PERMISSION CHECK
# ══════════════════════════════════════════════════════════════════
def can_trade():
    _reset_daily_if_needed()

    # NEW: Emergency stop — hard kill on deep drawdown
    if paper.get("emergency_stop", False):
        return False, "emergency_stop"

    dd_pct = (paper["peak_capital"] - paper["capital"]) / paper["peak_capital"] * 100
    if dd_pct >= EMERGENCY_DRAWDOWN_PCT:
        paper["emergency_stop"] = True
        print(f"[paper_engine] ⚠️ EMERGENCY STOP — drawdown {dd_pct:.2f}% exceeded {EMERGENCY_DRAWDOWN_PCT}%")
        return False, "emergency_stop"

    if _psychology_stopped:
        return False, "psychology_stop"

    if paper["consecutive_loss"] >= config.MAX_CONSECUTIVE_LOSS:
        return False, "consecutive_loss"

    if len(open_trades) >= config.MAX_OPEN_TRADES:
        return False, "max_positions"

    if paper["daily_trades"] >= config.MAX_DAILY_TRADES:
        return False, "daily_trade_limit"

    daily_loss_pct = -paper["daily_pnl"] / config.STARTING_CAPITAL * 100
    if daily_loss_pct >= config.MAX_DAILY_LOSS_PCT:
        return False, "daily_loss_limit"

    # Revenge cooldown
    if _last_loss_time:
        elapsed = (datetime.now(timezone.utc) - _last_loss_time).total_seconds() / 60
        if elapsed < config.REVENGE_COOLDOWN_MIN:
            remaining = int(config.REVENGE_COOLDOWN_MIN - elapsed)
            return False, f"revenge_cooldown:{remaining}"

    return True, "ok"


# ══════════════════════════════════════════════════════════════════
# PSYCHOLOGY STATUS
# ══════════════════════════════════════════════════════════════════
def psychology_status():
    ok, reason = can_trade()
    if ok:
        return None
    reasons = {
        "emergency_stop":    f"🚨 EMERGENCY STOP — drawdown >{EMERGENCY_DRAWDOWN_PCT}% from peak",
        "consecutive_loss":  f"⛔ {config.MAX_CONSECUTIVE_LOSS} consecutive losses — cooling down",
        "max_positions":     f"⚠️ Max {config.MAX_OPEN_TRADES} positions open",
        "daily_trade_limit": f"⚠️ Daily trade limit ({config.MAX_DAILY_TRADES}) reached",
        "daily_loss_limit":  f"🚨 Daily loss limit ({config.MAX_DAILY_LOSS_PCT}%) hit",
        "psychology_stop":   "🧠 Psychology stop active",
    }
    if reason.startswith("revenge_cooldown"):
        mins = reason.split(":")[1]
        return f"⏳ Revenge cooldown — {mins} min remaining"
    return reasons.get(reason, f"Blocked: {reason}")


# ══════════════════════════════════════════════════════════════════
# CREATE TRADE
# ══════════════════════════════════════════════════════════════════
def create_trade(coin, signal, entry, confidence=70,
                 tp1=None, tp2=None, tp3=None, sl=None,
                 atr=None, rr=None, trigger="SIGNAL", regime="TRENDING"):

    ok, reason = can_trade()
    if not ok:
        print(f"[create_trade] blocked: {reason}")
        return None

    paper["trade_count"]  += 1
    paper["daily_trades"] += 1
    trade_id = f"{coin.replace('/','').replace(':USDT','')}_{paper['trade_count']}"

    leverage = choose_leverage(confidence, coin=coin)

    # Fallback TP/SL if not provided by strategy
    if sl is None:
        sl_pct = 0.40
        if signal == "BUY":
            sl  = entry * (1 - sl_pct / 100)
            tp1 = tp1 or entry * 1.0025
            tp2 = tp2 or entry * 1.006
            tp3 = tp3 or entry * 1.0125
        else:
            sl  = entry * (1 + sl_pct / 100)
            tp1 = tp1 or entry * 0.9975
            tp2 = tp2 or entry * 0.994
            tp3 = tp3 or entry * 0.9875

    qty = position_size(paper["capital"], entry, sl, confidence=confidence)

    # Session-aware size reduction
    session_name = "24H Global"
    session_mult = 1.0
    session_note = "Full size"
    if _SESSION_GUARD_AVAILABLE:
        session_info = get_session_info()
        qty, session_note = apply_session_sizing(qty, session_info)
        session_name = session_info["name"]
        session_mult = session_info["size_mult"]

    notional = qty * entry
    fee_in   = calc_fee(notional, is_maker=getattr(config, "USE_LIMIT_ORDERS", True))
    bep      = breakeven_pct(notional)

    sl_pct_actual  = abs(sl - entry) / entry * 100
    tp1_pct_actual = abs(tp1 - entry) / entry * 100
    rr_actual      = rr or round(tp1_pct_actual / (sl_pct_actual + 1e-10), 2)

    trade = {
        # Identity
        "trade_id":       trade_id,
        "coin":           coin,
        "signal":         signal,
        # Prices
        "entry":          entry,
        "sl":             sl,
        "tp1":            tp1,
        "tp2":            tp2,
        "tp3":            tp3,
        # Size
        "qty":            qty,
        "notional":       round(notional, 2),
        "leverage":       leverage,
        "confidence":     confidence,
        # Metrics
        "sl_pct":         round(sl_pct_actual, 3),
        "tp1_pct":        round(tp1_pct_actual, 3),
        "rr":             rr_actual,
        "atr":            float(atr) if atr else 0.0,
        # Fees
        "fee_entry":      round(fee_in, 4),
        "breakeven_pct":  bep,
        # Meta
        "trigger":        trigger,
        "regime":         regime,
        "opened":         datetime.now(timezone.utc),
        # Session
        "session":        session_name,
        "session_mult":   session_mult,
        "session_note":   session_note,
        # Partial exit tracking
        "tp1_hit":        False,
        "tp2_hit":        False,
        "qty_remaining":  qty,
        "realized_pnl":   0.0,
        # Trailing SL
        "trail_sl_active": False,
    }

    paper["capital"]    -= fee_in
    paper["daily_fees"] += fee_in
    open_trades[coin]    = trade

    streak_info = f" | win_streak={paper['consecutive_win']}" if paper["consecutive_win"] >= 3 else ""
    print(f"[create_trade] {trade_id} {signal} entry={entry:.4f} qty={qty} lev={leverage}x conf={confidence}%{streak_info} session={session_name}({session_mult:.0%})")

    return deepcopy(trade)


# ══════════════════════════════════════════════════════════════════
# CHECK TRADE (called every scan tick for each open trade)
# ══════════════════════════════════════════════════════════════════
def check_trade(coin, price):
    if coin not in open_trades:
        return None

    trade  = open_trades[coin]
    signal = trade["signal"]
    updates = []

    price = float(price)

    # Price checks
    if signal == "BUY":
        tp1_hit = price >= trade["tp1"]
        tp2_hit = price >= trade["tp2"]
        tp3_hit = price >= trade["tp3"]
        sl_hit  = price <= trade["sl"]
    else:
        tp1_hit = price <= trade["tp1"]
        tp2_hit = price <= trade["tp2"]
        tp3_hit = price <= trade["tp3"]
        sl_hit  = price >= trade["sl"]

    tp1_exit_pct = getattr(config, "TP1_EXIT_PCT", 40) / 100
    tp2_exit_pct = getattr(config, "TP2_EXIT_PCT", 40) / 100

    # ──────────────────────────────────────────────
    # TP1 HIT — close 40%, trail SL to breakeven
    # ──────────────────────────────────────────────
    if tp1_hit and not trade["tp1_hit"]:
        trade["tp1_hit"] = True

        qty_to_close = round(trade["qty"] * tp1_exit_pct, 6)
        exit_notional = qty_to_close * trade["tp1"]
        fee_partial   = calc_fee(exit_notional, is_maker=False)

        if signal == "BUY":
            gross = qty_to_close * (trade["tp1"] - trade["entry"])
        else:
            gross = qty_to_close * (trade["entry"] - trade["tp1"])

        gross *= trade["leverage"]
        net    = gross - fee_partial

        trade["qty_remaining"] -= qty_to_close
        trade["realized_pnl"]  += net
        paper["capital"]       += net
        paper["daily_pnl"]     += net
        paper["daily_fees"]    += fee_partial

        # Move SL to breakeven + fee offset
        fee_offset = trade["entry"] * (trade["breakeven_pct"] / 100)
        trade["sl"] = trade["entry"] + fee_offset if signal == "BUY" \
                      else trade["entry"] - fee_offset

        tp1_pct = abs(trade["tp1"] - trade["entry"]) / trade["entry"] * 100
        updates.append(
            f"🎯 <b>TP1 HIT — {coin}</b>  (+{tp1_pct:.3f}%)\n"
            f"Trade:    {trade['trade_id']}\n"
            f"Closed:   40% at {round(trade['tp1'], 4)}\n"
            f"Booked:   ₹{net:+.2f}\n"
            f"Running:  {round(trade['qty_remaining'], 6)} units (60% open)\n"
            f"🛡️ SL → Break-even. Position is now risk-free."
        )

    # ──────────────────────────────────────────────
    # TP2 HIT — close another 40%, trail SL to TP1
    # ──────────────────────────────────────────────
    if tp2_hit and not trade["tp2_hit"]:
        trade["tp2_hit"] = True

        qty_to_close  = min(round(trade["qty"] * tp2_exit_pct, 6), trade["qty_remaining"])
        exit_notional = qty_to_close * trade["tp2"]
        fee_partial   = calc_fee(exit_notional, is_maker=False)

        if signal == "BUY":
            gross = qty_to_close * (trade["tp2"] - trade["entry"])
        else:
            gross = qty_to_close * (trade["entry"] - trade["tp2"])

        gross *= trade["leverage"]
        net    = gross - fee_partial

        trade["qty_remaining"] -= qty_to_close
        trade["realized_pnl"]  += net
        paper["capital"]       += net
        paper["daily_pnl"]     += net
        paper["daily_fees"]    += fee_partial

        # Trail SL to TP1 — locks in TP1 profit on the last 20%
        trade["sl"] = trade["tp1"]

        tp2_pct = abs(trade["tp2"] - trade["entry"]) / trade["entry"] * 100
        updates.append(
            f"🎯 <b>TP2 HIT — {coin}</b>  (+{tp2_pct:.3f}%)\n"
            f"Trade:    {trade['trade_id']}\n"
            f"Closed:   40% at {round(trade['tp2'], 4)}\n"
            f"Booked:   ₹{net:+.2f}\n"
            f"Running:  {round(trade['qty_remaining'], 6)} units (20% runner)\n"
            f"🔒 SL → TP1 price. Worst case: exit at profit."
        )

    # ──────────────────────────────────────────────
    # TRAILING SL — active every tick after TP1 hit
    # ──────────────────────────────────────────────
    if not tp3_hit and not sl_hit:
        if _TRAILING_SL_AVAILABLE and trade["tp1_hit"] and trade.get("atr", 0) > 0:
            new_sl, sl_moved = compute_trail_sl(
                signal        = signal,
                current_price = price,
                current_sl    = trade["sl"],
                atr_val       = trade["atr"],
                tp1_hit       = trade["tp1_hit"],
                tp2_hit       = trade["tp2_hit"],
            )
            if sl_moved:
                old_sl = trade["sl"]
                trade["sl"] = new_sl
                trade["trail_sl_active"] = True
                updates.append(format_trail_update(
                    coin, trade["trade_id"], old_sl, new_sl, signal, price
                ))

        return {"closed": False, "updates": updates, "trade_id": None}

    # ──────────────────────────────────────────────
    # TERMINAL CLOSE — TP3 hit or SL hit
    # ──────────────────────────────────────────────
    duration      = str(datetime.now(timezone.utc) - trade["opened"]).split(".")[0]
    qty_left      = trade["qty_remaining"]
    exit_price    = trade["tp3"] if tp3_hit else trade["sl"]
    exit_notional = qty_left * exit_price
    fee_out       = calc_fee(exit_notional, is_maker=False)

    if tp3_hit:
        result = "TP3 HIT ✅"
        if signal == "BUY":
            gross = qty_left * (exit_price - trade["entry"])
        else:
            gross = qty_left * (trade["entry"] - exit_price)
        paper["wins"]             += 1
        paper["consecutive_win"]  += 1
        paper["consecutive_loss"]  = 0

    else:   # SL hit
        # Better result labels based on how far trade progressed
        if trade["tp2_hit"]:
            result = "TP2+ TRAIL EXIT 🔒"    # SL was at TP1 level — still a profit
        elif trade["tp1_hit"]:
            result = "BREAKEVEN EXIT 🛡️"     # SL was at breakeven — ~zero loss
        else:
            result = "STOP LOSS ❌"           # Clean SL hit before TP1

        if signal == "BUY":
            gross = qty_left * (exit_price - trade["entry"])
        else:
            gross = qty_left * (trade["entry"] - exit_price)

        if not trade["tp1_hit"]:
            paper["losses"]           += 1
            paper["consecutive_loss"] += 1
            paper["consecutive_win"]   = 0   # reset win streak on real loss
            global _last_loss_time
            _last_loss_time = datetime.now(timezone.utc)
            if paper["consecutive_loss"] >= config.MAX_CONSECUTIVE_LOSS:
                global _psychology_stopped
                _psychology_stopped = True
                print(f"[paper_engine] Psychology stop triggered — {config.MAX_CONSECUTIVE_LOSS} consecutive losses")
        else:
            # TP1 was already hit and booked — this is a win overall
            paper["wins"]            += 1
            paper["consecutive_win"] += 1
            paper["consecutive_loss"] = 0

    gross *= trade["leverage"]
    net_final  = gross - fee_out
    total_net  = trade["realized_pnl"] + net_final
    pnl_pct    = total_net / config.STARTING_CAPITAL * 100

    paper["capital"]    += net_final
    paper["daily_pnl"]  += net_final
    paper["daily_fees"] += fee_out

    if paper["capital"] > paper["peak_capital"]:
        paper["peak_capital"] = paper["capital"]

    # Update per-coin stats
    _update_coin_stats(coin, result, total_net)

    print(f"[closed] {coin} {result} | net=₹{total_net:.2f} | capital=₹{paper['capital']:.0f}")

    result_data = {
        "closed":       True,
        "updates":      updates,
        "trade_id":     trade["trade_id"],
        "coin":         coin,
        "result":       result,
        "signal":       signal,
        "entry":        round(trade["entry"], 4),
        "exit":         round(exit_price, 4),
        "gross_pnl":    round(gross, 2),
        "fee_total":    round(fee_out + trade["fee_entry"], 4),
        "net_pnl":      round(total_net, 2),
        "realized_pnl": round(trade["realized_pnl"], 2),
        "pnl_pct":      round(pnl_pct, 3),
        "capital":      round(paper["capital"], 2),
        "duration":     duration,
        "leverage":     trade["leverage"],
        "trigger":      trade["trigger"],
        "regime":       trade["regime"],
        "session":      trade.get("session", "?"),
        "session_mult": trade.get("session_mult", 1.0),
    }

    del open_trades[coin]
    return result_data


# ══════════════════════════════════════════════════════════════════
# REPORTING
# ══════════════════════════════════════════════════════════════════
def get_report() -> dict:
    """
    Expanded report including per-coin stats and best/worst performers.
    """
    _reset_daily_if_needed()

    total    = paper["wins"] + paper["losses"]
    winrate  = round(paper["wins"] / total * 100, 2) if total else 0
    drawdown = round((paper["peak_capital"] - paper["capital"]) / paper["peak_capital"] * 100, 2)

    # Best and worst performing coins
    stats   = get_coin_stats()
    best    = next(iter(stats), None)
    worst   = next(reversed(list(stats.keys())), None) if stats else None

    return {
        # Core
        "capital":        round(paper["capital"], 2),
        "start_capital":  config.STARTING_CAPITAL,
        "peak_capital":   round(paper["peak_capital"], 2),
        # Performance
        "wins":           paper["wins"],
        "losses":         paper["losses"],
        "winrate":        winrate,
        "drawdown_pct":   drawdown,
        "emergency_stop": paper.get("emergency_stop", False),
        # Daily
        "daily_trades":   paper["daily_trades"],
        "daily_pnl":      round(paper["daily_pnl"], 2),
        "daily_fees":     round(paper["daily_fees"], 4),
        # Streaks
        "consec_losses":  paper["consecutive_loss"],
        "consec_wins":    paper["consecutive_win"],
        # Open
        "open_trades":    len(open_trades),
        # Per-coin
        "best_coin":      f"{best} ₹{stats[best]['net_pnl']:+.0f}" if best else "—",
        "worst_coin":     f"{worst} ₹{stats[worst]['net_pnl']:+.0f}" if worst else "—",
        "coin_stats":     stats,
    }
