# ╔══════════════════════════════════════════════════════════════╗
# ║  trailing_sl.py  —  ATR Trailing Stop Loss v2.0              ║
# ║                                                              ║
# ║  FIXES vs v1.0:                                              ║
# ║  • Minimum movement threshold — SL must move at least       ║
# ║    0.1× ATR before a Telegram update is sent.               ║
# ║    Prevents message flood on every tick after TP1.          ║
# ║  • TP2 tighter trail multiplier preserved (0.48× ATR)       ║
# ╚══════════════════════════════════════════════════════════════╝

import config

TRAIL_ATR_MULT    = 0.8
MIN_MOVE_FRACTION = 0.10   # SL must move at least 10% of ATR before Telegram update


def compute_trail_sl(
    signal: str,
    current_price: float,
    current_sl: float,
    atr_val: float,
    tp1_hit: bool,
    tp2_hit: bool,
) -> tuple[float, bool]:
    """
    Computes the new trailing SL. Returns (new_sl, was_updated).
    SL only moves in the profit direction. Telegram update only triggered
    if the move is >= MIN_MOVE_FRACTION × ATR (avoids flooding Telegram).
    """
    if not tp1_hit:
        return current_sl, False

    atr_val       = float(atr_val)
    current_price = float(current_price)
    current_sl    = float(current_sl)

    mult       = TRAIL_ATR_MULT * 0.6 if tp2_hit else TRAIL_ATR_MULT
    trail_dist = atr_val * mult

    # Minimum meaningful move before we report the update
    min_move = atr_val * MIN_MOVE_FRACTION

    if signal == "BUY":
        candidate_sl = current_price - trail_dist
        if candidate_sl > current_sl:
            moved_by = candidate_sl - current_sl
            significant = moved_by >= min_move
            return round(candidate_sl, 8), significant
    else:
        candidate_sl = current_price + trail_dist
        if candidate_sl < current_sl:
            moved_by = current_sl - candidate_sl
            significant = moved_by >= min_move
            return round(candidate_sl, 8), significant

    return current_sl, False


def format_trail_update(coin: str, trade_id: str, old_sl: float, new_sl: float,
                        signal: str, price: float) -> str:
    direction = "↑" if signal == "BUY" else "↓"
    locked    = abs(new_sl - old_sl)
    return (
        f"🔄 <b>TRAIL SL — {coin}</b>\n"
        f"Trade: {trade_id}\n"
        f"Price:  {round(price, 4)}\n"
        f"SL: {round(old_sl, 4)} → {round(new_sl, 4)} {direction}\n"
        f"Locked: +{locked:.5f} more profit protected"
    )
