# ╔══════════════════════════════════════════════════════════════╗
# ║  config.py  —  Central configuration v8.0                    ║
# ╚══════════════════════════════════════════════════════════════╝

# ── Telegram
BOT_TOKEN  = "8678921069:AAE2fEtnTIVjfGMpDMJIlzUtxqyBAb-FV_w"
CHAT_ID    = "5916055079"

# ── CryptoPanic
CRYPTOPANIC_TOKEN = ""

# ── Symbols
SYMBOLS = [
    "BTC/USDT",   "ETH/USDT",   "SOL/USDT",   "XRP/USDT",   "ADA/USDT",
    "AVAX/USDT",  "DOT/USDT",   "LINK/USDT",  "MATIC/USDT", "NEAR/USDT",
    "OP/USDT",    "APT/USDT",   "SUI/USDT",   "INJ/USDT",   "DOGE/USDT",
    "SHIB/USDT",  "LTC/USDT",   "TRX/USDT",   "ATOM/USDT",  "FET/USDT"
]

# ── Timeframes
ENTRY_TIMEFRAME = "3m"
TREND_TIMEFRAME = "15m"
FAST_TIMEFRAME  = "1m"

# ── Paper trading
PAPER_MODE       = True
STARTING_CAPITAL = 100000

# ── Risk management
MAX_DAILY_LOSS_PCT    = 4.0
MAX_OPEN_TRADES       = 3
MAX_CONSECUTIVE_LOSS  = 3
MAX_DAILY_TRADES      = 20

# ── Leverage tiers
LEVERAGE_HIGH     = 3
LEVERAGE_MED      = 2
LEVERAGE_LOW      = 1
MAX_LEVERAGE      = 3
MAX_LEVERAGE_ALTS = 2
MAJOR_COINS       = ["BTC/USDT", "ETH/USDT"]

# ── Partial exit strategy
TP1_EXIT_PCT = 40
TP2_EXIT_PCT = 40

# ── Dynamic risk sizing
RISK_PER_TRADE_PCT     = 0.8
RISK_BOOST_HIGH_CONF   = 1.1
HIGH_CONF_BOOST_THRESH = 80

# ── TP / SL levels (ATR-based multipliers)
ATR_SL    = 0.8
ATR_TP1   = 1.5
ATR_TP2   = 2.8
ATR_TP3   = 5.0
MAX_SL_PCT   = 0.6
MIN_RR_RATIO = 1.5

# ── Fee structure
MAKER_FEE_PCT    = 0.02
TAKER_FEE_PCT    = 0.05
USE_BNB_FEES     = True
USE_LIMIT_ORDERS = True

# ── Signal quality gates
# NOTE: MIN_CONFIDENCE raised to 65 now that stoch_k/ADX/Ichimoku are
# properly included — the scorer is more accurate, so a 65 here is
# genuinely equivalent to the old 62 (which had broken dim-6 scoring).
MIN_CONFIDENCE      = 65
MIN_BULL_BEAR_SCORE = 4
VOLUME_MULT         = 0.9
SPIKE_VOLUME_MULT   = 1.8
AVOID_SIDEWAYS      = True
MIN_TREND_STRENGTH  = 8

# ── ADX filter (new in v8.0)
# Minimum ADX for entries. Below this = directionless chop, skip.
MIN_ADX = 18

# ── Multi-timeframe confirmation
MTF_CONFIRM_ON = True

# ── Order book analysis
ORDERBOOK_DEPTH  = 20
MIN_WALL_RATIO   = 3.5
ORDERBOOK_GATE_ON = True

# ── Funding rate gate
MAX_FUNDING_RATE_PCT = 0.05

# ── Spread gate
MAX_SPREAD_PCT = 0.04

# ── Trading sessions
SESSION_FILTER_ON = False
TRADING_SESSIONS = [
    {"name": "Asia",       "start":  0, "end":  4, "quality": 2},
    {"name": "London",     "start":  7, "end": 12, "quality": 4},
    {"name": "NY Overlap", "start": 13, "end": 17, "quality": 5},
    {"name": "NY Close",   "start": 19, "end": 22, "quality": 3},
]

# ── Psychology / discipline
REVENGE_COOLDOWN_MIN = 20
MAX_CONSEC_LOSSES    = 3

# ── News filter
NEWS_FILTER_ON   = True
PAUSE_BEFORE_MIN = 30
PAUSE_AFTER_MIN  = 30
FEAR_GREED_LOW   = 15
FEAR_GREED_HIGH  = 90

# ── Operational
SCAN_INTERVAL      = 10
SIGNAL_COOLDOWN    = 480
CANDLE_LIMIT       = 200
DAILY_SUMMARY_HOUR = 0

# ── API Keys
CRYPTOCOMPARE_KEY = "e85fa6bad3da72eb801c15eaf24b16d84742fa5655a99e739e5ae063f23fafc7"
