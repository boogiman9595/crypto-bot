# ╔══════════════════════════════════════════════════════════════╗
# ║  session_guard.py  —  Session-Aware Position Sizing v1.0     ║
# ║                                                              ║
# ║  PURPOSE: Low-liquidity trading hours have wider spreads,    ║
# ║  thinner order books, and more aggressive wick hunting.      ║
# ║  Instead of completely blocking off-peak trades, we reduce   ║
# ║  position size so risk is proportionally smaller.            ║
# ║                                                              ║
# ║  Sessions (UTC):                                             ║
# ║  • Asia Open    00:00-04:00  → 70% size  (thin liquidity)   ║
# ║  • Dead Hours   04:00-07:00  → 50% size  (very thin)        ║
# ║  • London Open  07:00-09:00  → 90% size  (picking up)       ║
# ║  • London Peak  09:00-12:00  → 100% size (full)             ║
# ║  • NY Overlap   13:00-17:00  → 100% size (best session)     ║
# ║  • NY Afternoon 17:00-20:00  → 85% size  (still good)       ║
# ║  • NY Close     20:00-22:00  → 75% size  (winding down)     ║
# ║  • Late Night   22:00-00:00  → 55% size  (very thin)        ║
# ║                                                              ║
# ║  Also returns the session quality label for Telegram alerts. ║
# ╚══════════════════════════════════════════════════════════════╝

from datetime import datetime, timezone


# Session definitions: (start_hour_utc, end_hour_utc, name, size_multiplier, quality_stars)
SESSIONS = [
    (0,  4,  "Asia Open 🌏",      0.70, "⭐⭐"),
    (4,  7,  "Dead Hours 💤",     0.50, "⭐"),
    (7,  9,  "London Open 🇬🇧",  0.90, "⭐⭐⭐⭐"),
    (9,  12, "London Peak 🇬🇧",  1.00, "⭐⭐⭐⭐⭐"),
    (12, 13, "Lunch Lull 🍽️",    0.75, "⭐⭐⭐"),
    (13, 17, "NY Overlap 🗽",     1.00, "⭐⭐⭐⭐⭐"),
    (17, 20, "NY Afternoon 🗽",   0.85, "⭐⭐⭐⭐"),
    (20, 22, "NY Close 🌆",       0.75, "⭐⭐⭐"),
    (22, 24, "Late Night 🌙",     0.55, "⭐"),
]

# Minimum size multiplier — never go below this even in worst session
MIN_SIZE_MULT = 0.50

# Threshold below which we add a caution note to the Telegram signal
CAUTION_THRESHOLD = 0.70


def get_session_info() -> dict:
    """
    Returns current session information including position size multiplier.

    Returns dict with:
      name:         session name string
      size_mult:    float (0.5-1.0) — multiply position qty by this
      quality:      star string
      hour_utc:     current UTC hour
      is_peak:      True if size_mult == 1.0
      caution:      True if size_mult < CAUTION_THRESHOLD
    """
    hour = datetime.now(timezone.utc).hour

    for start, end, name, mult, stars in SESSIONS:
        if start <= hour < end:
            return {
                "name":      name,
                "size_mult": mult,
                "quality":   stars,
                "hour_utc":  hour,
                "is_peak":   mult >= 1.0,
                "caution":   mult < CAUTION_THRESHOLD,
            }

    # Fallback (shouldn't happen with 0-24 coverage)
    return {
        "name":      "Unknown",
        "size_mult": 0.70,
        "quality":   "⭐⭐",
        "hour_utc":  hour,
        "is_peak":   False,
        "caution":   False,
    }


def apply_session_sizing(base_qty: float, session_info: dict) -> tuple[float, str]:
    """
    Applies session size multiplier to base position quantity.

    Returns (adjusted_qty, note_for_telegram).

    Integration in paper_engine.py create_trade():
        from session_guard import get_session_info, apply_session_sizing
        session_info = get_session_info()
        qty, session_note = apply_session_sizing(qty, session_info)
        # store session_note in trade dict for Telegram display
    """
    mult = max(session_info["size_mult"], MIN_SIZE_MULT)
    adjusted = round(base_qty * mult, 6)

    if mult >= 1.0:
        note = f"📊 Peak session — full size"
    elif mult >= 0.85:
        note = f"📊 Good session — {int(mult*100)}% size ({session_info['name']})"
    elif mult >= 0.70:
        note = f"⚠️ Off-peak — {int(mult*100)}% size ({session_info['name']})"
    else:
        note = f"⚠️ Low liquidity — {int(mult*100)}% size ({session_info['name']})"

    return adjusted, note


def should_skip_session(min_quality_mult: float = 0.50) -> tuple[bool, str]:
    """
    Returns (True, reason) if current session is below minimum quality.
    Default: never skip (min_quality_mult=0.50), just reduce size.

    To enable hard session blocking, call with a higher threshold:
        skip, reason = should_skip_session(min_quality_mult=0.60)
    """
    info = get_session_info()
    if info["size_mult"] < min_quality_mult:
        return True, f"Session {info['name']} too low quality ({info['size_mult']:.0%} size)"
    return False, f"session_ok: {info['name']} {info['quality']}"


def format_session_line(session_info: dict) -> str:
    """Formats a one-line session summary for Telegram signal messages."""
    mult = session_info["size_mult"]
    size_str = "Full size" if mult >= 1.0 else f"{int(mult*100)}% size"
    return f"{session_info['name']} {session_info['quality']} — {size_str}"
