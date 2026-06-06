# ╔══════════════════════════════════════════════════════════════╗
# ║  telegram_sender.py  —  All message formatters v7.0          ║
# ║                                                              ║
# ║  UPGRADES vs v6:                                             ║
# ║  • signal_message now shows ADX, vol_regime, SL method,     ║
# ║    RSI divergence, Ichimoku bias                             ║
# ║  • daily_summary now shows per-coin stats (best/worst)      ║
# ║  • startup updated to v8.0 feature list                     ║
# ╚══════════════════════════════════════════════════════════════╝

import requests
import config

BOT_TOKEN = config.BOT_TOKEN
CHAT_ID   = config.CHAT_ID


def send(message, retries=3):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    for attempt in range(retries):
        try:
            r = requests.post(url, data={
                "chat_id":    CHAT_ID,
                "text":       message,
                "parse_mode": "HTML",
            }, timeout=20)
            if r.status_code == 200:
                return True
            print(f"[Telegram] status {r.status_code}: {r.text[:100]}")
        except Exception as e:
            print(f"[Telegram] attempt {attempt+1} error: {e}")
    return False


def _fp(price):
    if price is None: return "N/A"
    if price < 0.0001: return f"{price:.8f}"
    if price < 0.01:   return f"{price:.6f}"
    if price < 1:      return f"{price:.5f}"
    if price < 100:    return f"{price:.4f}"
    return f"{price:.2f}"


def _pct(a, b):
    if b == 0: return 0
    return abs(a - b) / b * 100


# ══════════════════════════════════════════════════════════════════
# STARTUP
# ══════════════════════════════════════════════════════════════════
def startup_message(capital=None, sentiment="😐 Neutral", session="Starting"):
    cap  = capital or config.STARTING_CAPITAL
    mode = "🟡 PAPER" if config.PAPER_MODE else "🔴 LIVE"
    pairs = " | ".join([s.replace("/USDT","") for s in config.SYMBOLS])
    send(f"""
🤖 <b>SCALP BOT v8.0 ONLINE</b>
━━━━━━━━━━━━━━━━━━━━━━
Capital:    ₹{cap:,.0f} INR
Pairs:      {pairs}
Mode:       {mode} | FUTURES
Session:    {session}
Sentiment:  {sentiment}
━━━━━━━━━━━━━━━━━━━━━━
✅ <b>Active Filters (v8.0):</b>
  • ATR TP/SL + Structure-aware SL
  • 3-TF + HTF (1h/4h) confirmation
  • ADX trend strength gate (≥18)
  • RSI divergence detection
  • Ichimoku cloud bias (15m)
  • Volatility regime classifier
  • Pre-entry quality guard (5 checks)
  • OFI order book imbalance gate
  • Signal quality scorer (6 dimensions)
  • News + Fear/Greed guard
  • Session-aware sizing
  • Psychology discipline rules
  • Trailing SL (post-TP1 only)
  • Emergency drawdown kill (8%)
━━━━━━━━━━━━━━━━━━━━━━
⚡ Scanning every {config.SCAN_INTERVAL}s…
""".strip())


# ══════════════════════════════════════════════════════════════════
# SIGNAL MESSAGE
# ══════════════════════════════════════════════════════════════════
def signal_message(coin, trade, sig, sentiment="😐 Neutral", session="NY Overlap",
                   ofi=None, news_tier="LOW"):
    direction  = "🟢 LONG" if trade["signal"] == "BUY" else "🔴 SHORT"
    conf       = int(float(trade["confidence"]))
    bar        = "█" * (conf // 10) + "░" * (10 - conf // 10)

    tp1_pct = _pct(trade["tp1"], trade["entry"])
    tp2_pct = _pct(trade["tp2"], trade["entry"])
    tp3_pct = _pct(trade["tp3"], trade["entry"])
    sl_pct  = trade.get("sl_pct", _pct(trade["sl"], trade["entry"]))
    rr      = trade.get("rr", round(tp1_pct / (sl_pct + 1e-9), 2))
    lev     = trade["leverage"]

    base_capital = float(getattr(config, "STARTING_CAPITAL", 100000.0))
    fraction_ratio = (conf / 100.0) * 0.5
    margin_inr     = round(base_capital * fraction_ratio, 2)
    if margin_inr > base_capital / 3.0:
        margin_inr = round(base_capital / 4.0, 2)
    notional_inr = margin_inr * lev
    entry_price  = float(trade["entry"])
    qty_crypto   = (notional_inr / 85.0) / entry_price if entry_price > 0 else 0.0

    fee    = trade.get("fee_entry", 0)
    bep    = trade.get("breakeven_pct", 0)
    trigger = trade.get("trigger", "SIGNAL")
    regime  = trade.get("regime", "TRENDING")
    atr     = trade.get("atr", sig.get("atr", 0))

    eff_tp1 = tp1_pct * lev
    eff_tp2 = tp2_pct * lev
    eff_tp3 = tp3_pct * lev
    eff_sl  = sl_pct  * lev

    rsi_label = ("🔵 Oversold"   if sig.get("rsi", 50) < 35
                 else "🟠 Overbought" if sig.get("rsi", 50) > 65
                 else "⚪ Neutral")

    pat_line  = f"\n  Pattern:     <b>{sig['pattern']}</b>" if sig.get("pattern") else ""
    vwap_dir  = "↑ Above" if trade["entry"] > sig.get("vwap", 0) else "↓ Below"

    ofi_label = (f"🟢 {ofi:+.2f} (bid-heavy)" if ofi and ofi > 0.1
                 else f"🔴 {ofi:+.2f} (ask-heavy)" if ofi and ofi < -0.1
                 else f"⚪ {ofi:+.2f} (neutral)"   if ofi is not None else "N/A")

    news_tier_icon = {"LOW": "🟢", "MEDIUM": "🟡", "HIGH": "🔴"}.get(news_tier, "⚪")

    # New fields
    adx_val  = sig.get("adx", 0)
    adx_str  = f"{adx_val:.0f} ({'Trending' if adx_val >= 25 else 'Weak trend'})"
    rsi_div  = sig.get("rsi_divergence", "")
    rsi_div_line = f"\n  RSI Div:     <b>{rsi_div}</b> 🔔" if rsi_div else ""
    ichi     = sig.get("ichimoku_bias", "NEUTRAL")
    ichi_icon = {"BULL": "🟢", "BEAR": "🔴", "NEUTRAL": "⚪"}.get(ichi, "⚪")
    vol_reg  = sig.get("vol_regime_label", "")
    vol_reg_line = f"\n  Vol Regime:  {vol_reg}" if vol_reg else ""
    sl_method = sig.get("sl_method", "atr")
    sl_type   = "📐 Structure" if "swing" in sl_method else "📏 ATR"

    send(f"""
⚡ <b>FUTURES SIGNAL v8 — {coin}</b>
━━━━━━━━━━━━━━━━━━━━━━━━━━━
{direction}  |  Score: <b>{sig.get('score',0):+d}/19</b>  |  Conf: <b>{conf}%</b>
[{bar}]

📌 Trigger:    <b>{trigger}</b>
📊 Regime:     <b>{regime}</b>
💪 Trend:      {sig.get('trend','?')} (str: {sig.get('trend_strength',0):.0f}/100)
📈 ADX:        {adx_str}
☁️ Ichimoku:   {ichi_icon} {ichi}
📅 Session:    {session}
🧠 Sentiment:  {sentiment}
📰 News:       {news_tier_icon} {news_tier}
📊 OFI:        {ofi_label}{rsi_div_line}{vol_reg_line}

💰 <b>Entry:</b>  {_fp(trade['entry'])}
🛑 <b>SL Type:</b> {sl_type}  ({sl_method[:30]})
📐 ATR:     {_fp(atr)}  ({sig.get('atr_pct',0):.3f}%)
🔊 Volume:  {sig.get('vol_trend','?')}  {'🔥SPIKE' if sig.get('vol_spike') else ''}

🛍️ <b>Position Size:</b>
  Margin:       <b>₹{margin_inr:,.2f} INR</b>
  w/ Leverage:  ₹{notional_inr:,.2f} ({lev}x)
  Units:        {qty_crypto:.4f} {coin.split('/')[0]}

🎯 <b>Take Profits:</b>
  TP1  →  {_fp(trade['tp1'])}  <i>(+{tp1_pct:.3f}% | ×{lev} = +{eff_tp1:.2f}%)</i>
  TP2  →  {_fp(trade['tp2'])}  <i>(+{tp2_pct:.3f}% | ×{lev} = +{eff_tp2:.2f}%)</i>
  TP3  →  {_fp(trade['tp3'])}  <i>(+{tp3_pct:.3f}% | ×{lev} = +{eff_tp3:.2f}%)</i>

🛑 <b>Stop Loss:</b>  {_fp(trade['sl'])}  <i>(-{sl_pct:.3f}% | ×{lev} = -{eff_sl:.2f}%)</i>
⚖️ <b>R:R Ratio:</b>  1 : {rr}
🏦 <b>Leverage:</b>   {lev}x  (isolated margin)

💸 <b>Fees:</b>  Entry -₹{fee:.2f}  |  BEP >{bep:.4f}%

📊 <b>Indicators:</b>
  RSI(14):   {sig.get('rsi',0):.1f}  [{rsi_label}]
  Stoch K/D: {sig.get('stoch_k',0):.0f} / {sig.get('stoch_d',0):.0f}
  MACD Hist: {sig.get('macd_hist',0):.6f}
  EMA 9/21:  {sig.get('ema9',0):.4f} / {sig.get('ema21',0):.4f}
  VWAP:      {_fp(sig.get('vwap',0))}  [{vwap_dir}]
  BB:        {_fp(sig.get('bb_lower',0))} — {_fp(sig.get('bb_upper',0))}{pat_line}

🆔 Trade ID:  {trade['trade_id']}
━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠️ <i>Paper trade. Isolated margin. DYOR.</i>
""".strip())


# ══════════════════════════════════════════════════════════════════
# TP UPDATES
# ══════════════════════════════════════════════════════════════════
def tp_update_message(updates):
    for msg in updates:
        if msg:
            send(msg)


# ══════════════════════════════════════════════════════════════════
# TRADE CLOSED
# ══════════════════════════════════════════════════════════════════
def trade_closed_message(result):
    emoji  = ("✅" if "TP3" in result["result"]
              else "🎯" if ("TP" in result["result"] or "BREAKEVEN" in result["result"] or "🔒" in result["result"])
              else "❌")
    c_gross = "🟢" if result["gross_pnl"] >= 0 else "🔴"
    c_net   = "🟢" if result["net_pnl"]   >= 0 else "🔴"
    realized = result.get("realized_pnl", 0)
    realized_line = f"  Partial exits: +₹{realized:.2f}\n" if realized > 0 else ""
    send(f"""
{emoji} <b>TRADE CLOSED — {result['coin']}</b>
━━━━━━━━━━━━━━━━━━━━━━━━━━━
Trade ID:    {result['trade_id']}
Direction:   {result['signal']}  ({result['leverage']}x)
Trigger:     {result.get('trigger','?')}
Regime:      {result.get('regime','?')}
Entry:       {_fp(result['entry'])}
Exit:        {_fp(result['exit'])}
Result:      <b>{result['result']}</b>
Duration:    {result['duration']}

💰 <b>P&amp;L:</b>
{realized_line}  Final close:  {c_gross} ₹{abs(result['gross_pnl']):.2f}
  Fees:        -₹{result['fee_total']:.4f}
  <b>Net:</b>         {c_net} ₹{abs(result['net_pnl']):.2f}  ({result['pnl_pct']:+.3f}%)

📊 Capital:    ₹{result['capital']:,.2f} INR
━━━━━━━━━━━━━━━━━━━━━━━━━━━
""".strip())


# ══════════════════════════════════════════════════════════════════
# NEWS / PSYCHOLOGY / SESSION / DAILY
# ══════════════════════════════════════════════════════════════════
def news_paused_message(news):
    icon = {"calendar":"⏰","fear_greed":"😱","headline":"🚨"}.get(news.get("source",""),"⚠️")
    send(f"""
{icon} <b>TRADING PAUSED — News Filter</b>
━━━━━━━━━━━━━━━━
Source:   {news.get('source','').upper()}
Reason:   {news.get('reason','')}
Detail:   {news.get('headline','')[:100]}
━━━━━━━━━━━━━━━━
Auto-resumes when clear ✅
""".strip())

def news_caution_message(news):
    send(f"""
⚠️ <b>CAUTION — Medium Risk News</b>
━━━━━━━━━━━━━━━━
{news.get('reason','')} | Tier: 🟡 MEDIUM
{news.get('headline','')[:100]}
━━━━━━━━━━━━━━━━
Trading with reduced confidence. Stay alert.
""".strip())

def news_resumed_message():
    send("▶️ <b>TRADING RESUMED</b> — All news filters clear.")

def psychology_message(reason):
    send(f"🧠 <b>DISCIPLINE PAUSE</b>\n━━━━━━━━━━━━━━\n{reason}")

def daily_summary_message(report, sentiment="😐 Neutral"):
    total = report["wins"] + report["losses"]
    wr    = report["winrate"]
    c     = "🟢" if report["daily_pnl"] >= 0 else "🔴"
    exp   = "✅ Positive" if wr > 50 and report["daily_pnl"] > 0 else "⚠️ Review"

    # Per-coin stats section
    coin_stats = report.get("coin_stats", {})
    top_coins  = list(coin_stats.items())[:5]  # top 5 by net P&L
    coins_line = ""
    for cn, cs in top_coins:
        sym = cn.replace("/USDT","")
        coins_line += f"  {sym}: ✅{cs['wins']} ❌{cs['losses']} ₹{cs['net_pnl']:+.0f}\n"

    send(f"""
📅 <b>DAILY SUMMARY v8</b>
━━━━━━━━━━━━━━━━━━━━━━
Signals:     {report.get('daily_trades',0)}
Trades:      {total}  (✅{report['wins']}  ❌{report['losses']})
Win rate:    {wr:.1f}%
Daily P&amp;L:   {c} ₹{abs(report['daily_pnl']):.2f}
Fees paid:   -₹{report['daily_fees']:.4f}
Drawdown:    {report['drawdown_pct']:.2f}%
Expectancy:  {exp}
Sentiment:   {sentiment}
Capital:     ₹{report['capital']:,.2f}
━━━━━━━━━━━━━━━━━━━━━━
🏆 <b>Best:</b>  {report.get('best_coin','—')}
📉 <b>Worst:</b> {report.get('worst_coin','—')}
<b>Coin Breakdown:</b>
{coins_line.rstrip() or '  No trades today'}
━━━━━━━━━━━━━━━━━━━━━━
""".strip())

def off_session_message(next_session):
    send(f"💤 <b>Off trading hours</b>\nNext: {next_session}\nBot monitoring only.")
