# ╔══════════════════════════════════════════════════════════════╗
# ║  news_filter.py  —  Live News + Fear/Greed + Calendar v6.6   ║
# ║                                                              ║
# ║  UPGRADE SUMMARY:                                            ║
# ║  • Removed restrictive session restrictions for 24/7 crypto  ║
# ║    market execution. Fixes "Off trading hours" false blocks. ║
# ║  • Reroutes Extreme Fear (< 12) from HIGH to MEDIUM tier.     ║
# ║  • Allows the bot to stay ACTIVE and take short/sell trades  ║
# ║    to secure heavy profits as the market dumps, utilizing    ║
# ║    a highly secure 0.5x risk size multiplier.                ║
# ╚══════════════════════════════════════════════════════════════╝

import requests
import time
import re
from datetime import datetime, timezone
import config


# ══════════════════════════════════════════════════════════════════
# TIERED KEYWORD LISTS
# ══════════════════════════════════════════════════════════════════

# ── HIGH PANIC — stop trading immediately (Exchange structural failure only)
HIGH_PANIC_WORDS = [
    "exchange hacked",
    "exchange down",
    "withdrawal suspended",
    "withdrawal halted",
    "exchange offline",
    "binance hacked",
    "coinbase hacked",
    "okx hacked",
    "binance shutdown",
    "coinbase shutdown",
    "stolen funds",
    "funds stolen",
    "exploit detected",
    "smart contract exploit",
    "bridge exploit",
    "protocol hacked",
    "hundreds of millions stolen",
    "millions drained",
    "rug pull",
    "exit scam",
    "ponzi scheme",
    "exchange insolvent",
    "exchange bankruptcy",
    "chapter 11 crypto",
    "crypto bankruptcy",
    "withdrawal freeze",
    "usdt depeg",
    "usdc depeg",
    "stablecoin depeg",
    "stablecoin collapse",
    "depegged",
    "bitcoin network down",
    "ethereum network down",
    "blockchain halted",
]

# ── MEDIUM PANIC — caution mode (Trade stays active with half risk)
# Ideal for catching aggressive shorts/selling cascades!
MEDIUM_PANIC_WORDS = [
    "sec charges crypto",
    "sec sues exchange",
    "sec lawsuit binance",
    "sec lawsuit coinbase",
    "crypto ban enacted",
    "bitcoin ban",
    "crypto trading ban",
    "government seizes crypto",
    "exchange arrested",
    "crypto executive arrested",
    "emergency rate hike",
    "rate hike surprise",
    "fomc emergency",
    "recession confirmed",
    "market circuit breaker",
    "sanctions crypto",
    "sanctions bitcoin",
    "war sanctions crypto",
    "nuclear threat markets",
    "liquidation cascade",
    "whale dump",
    "billion liquidated",
    "mass liquidation",
    "crypto market crash",
    "bitcoin crash",
    "crypto selloff",
    "major exchange fraud",
    "flash crash",
    "bitcoin flash crash",
    "crypto flash crash",
    "black swan crypto",
]


# ══════════════════════════════════════════════════════════════════
# ECONOMIC EVENTS
# ══════════════════════════════════════════════════════════════════
ECONOMIC_EVENTS = [
    {"name": "US CPI Release",  "tier": "HIGH",   "hour": 12, "minute": 30, "days": [0,1,2,3,4]},
    {"name": "FOMC Decision",   "tier": "HIGH",   "hour": 18, "minute":  0, "days": [0,1,2,3,4]},
    {"name": "NFP Jobs Report", "tier": "MEDIUM", "hour": 12, "minute": 30, "days": [4]},
]


# ══════════════════════════════════════════════════════════════════
# TRADING SESSIONS (Updated for 24/7 Crypto Market Coverage)
# ══════════════════════════════════════════════════════════════════
def _in_active_session() -> bool:
    """
    Crypto operates 24/7 worldwide. Returns True continuously so the bot 
    never locks itself out during profitable high-volume midnight moves.
    """
    return True


# ══════════════════════════════════════════════════════════════════
# CACHE STATE
# ══════════════════════════════════════════════════════════════════
_headline_cache    = []
_headline_cache_ts = 0.0
_CACHE_TTL         = 300    # refresh headlines every 5 min (during session)

_fg_cache          = {}
_fg_cache_ts       = 0.0
_FG_TTL            = 1800   # Fear/Greed: refresh every 30 min (it only updates 2x/day)

# Track if we already printed the "0 headlines" warning this session
_zero_headlines_warned = False


# ══════════════════════════════════════════════════════════════════
# KEYWORD MATCHING — context-aware, prevents false positives
# ══════════════════════════════════════════════════════════════════
def _matches(headline: str, phrase: str) -> bool:
    """
    Smart match to reduce false positives:
    - Phrases with spaces: exact substring match (already specific)
    - Single words: whole-word boundary match so "ban" doesn't
      match "abandon", "urban", "rebalance" etc.
    - Minimum headline length: skip very short noise headlines
    """
    if len(headline) < 20:          # skip noise/stub headlines
        return False
    if " " in phrase:               # multi-word phrase: direct match
        return phrase in headline
    else:                           # single word: whole-word match
        return bool(re.search(r'\b' + re.escape(phrase) + r'\b', headline))


# ══════════════════════════════════════════════════════════════════
# SOURCE 1 — Economic Calendar (offline, no network)
# ══════════════════════════════════════════════════════════════════
def _check_calendar():
    """
    Returns (tier, event_name, detail_string)
    tier = "HIGH" | "MEDIUM" | "LOW"
    """
    now  = datetime.now(timezone.utc)
    cur  = now.hour * 60 + now.minute
    wday = now.weekday()
    pb   = getattr(config, "PAUSE_BEFORE_MIN", 30)
    pa   = getattr(config, "PAUSE_AFTER_MIN",  30)

    for ev in ECONOMIC_EVENTS:
        if wday not in ev["days"]:
            continue
        evm  = ev["hour"] * 60 + ev["minute"]
        diff = cur - evm
        if -pb <= diff <= pa:
            detail = (f"{ev['name']} in {abs(diff)} min"
                      if diff < 0
                      else f"{ev['name']} {diff} min ago — settling")
            return ev["tier"], ev["name"], detail

    return "LOW", "", ""


# ══════════════════════════════════════════════════════════════════
# SOURCE 2 — Fear & Greed Index
# ══════════════════════════════════════════════════════════════════
def _fetch_fear_greed():
    global _fg_cache, _fg_cache_ts
    if time.time() - _fg_cache_ts < _FG_TTL and _fg_cache:
        return _fg_cache
    try:
        r    = requests.get(
            "https://api.alternative.me/fng/?limit=1",
            timeout=8,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        body = r.json()
        if (not isinstance(body, dict)
                or "data" not in body
                or not isinstance(body["data"], list)
                or len(body["data"]) == 0):
            return {"value": 50, "label": "Neutral"}
        entry  = body["data"][0]
        result = {
            "value": int(entry.get("value", 50)),
            "label": str(entry.get("value_classification", "Neutral")),
        }
        _fg_cache    = result
        _fg_cache_ts = time.time()
        return result
    except Exception as e:
        print(f"[fear_greed] {e}")
        return {"value": 50, "label": "Neutral"}


def _check_fear_greed():
    """
    MODIFIED LOGIC: Extreme fear is no longer treated as a hard shutdown.
    It routes into the 'MEDIUM' caution tier so our multicoin engine 
    can safely take short/sell trades as the market dips.
    """
    fg  = _fetch_fear_greed()
    val = fg["value"]
    lbl = fg["label"]
    
    low  = getattr(config, "FEAR_GREED_LOW",  12)
    high = getattr(config, "FEAR_GREED_HIGH", 92)

    if val < low:
        # Changed from "HIGH" block to "MEDIUM" caution tier
        return ("MEDIUM", "extreme_fear_active",
                f"Panic Conditions ({val}/100) — Scalping Selling Cascade with 0.5x Risk Sizes")
    if val > high:
        return ("MEDIUM", "extreme_greed",
                f"Extreme Greed ({val}/100) — bubble caution")
    return "LOW", lbl, str(val)


def get_fear_greed_label():
    fg   = _fetch_fear_greed()
    val  = fg["value"]
    lbl  = fg["label"]
    emos = {
        "Extreme Fear": "😱", "Fear": "😨",
        "Neutral": "😐", "Greed": "😊", "Extreme Greed": "🤑",
    }
    return f"{emos.get(lbl, '😐')} {lbl} ({val})"


# ══════════════════════════════════════════════════════════════════
# SOURCE 3 — CryptoCompare
# ══════════════════════════════════════════════════════════════════
def _fetch_cryptocompare():
    headlines = []
    try:
        api_key = getattr(config, "CRYPTOCOMPARE_KEY", "")
        url     = "https://min-api.cryptocompare.com/data/v2/news/?lang=EN"
        if api_key and api_key not in ("", "PASTE_YOUR_API_KEY_HERE"):
            url += f"&api_key={api_key}"

        r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})

        if r.status_code != 200:
            return headlines

        body     = r.json()
        msg_type = body.get("Type", 100) if isinstance(body, dict) else -1
        if msg_type != 100:
            return headlines   

        data = body.get("Data", [])
        if not isinstance(data, list):
            return headlines   

        count = 0
        for item in data:
            if count >= 25: break
            if isinstance(item, dict):
                title = item.get("title", "")
                if isinstance(title, str) and len(title.strip()) > 10:
                    headlines.append(title.lower().strip())
                    count += 1

        if count > 0:
            print(f"[CryptoCompare] ✅ {count} headlines")

    except requests.exceptions.Timeout:
        pass   
    except requests.exceptions.ConnectionError:
        pass   
    except Exception as e:
        print(f"[CryptoCompare] {e}")

    return headlines


# ══════════════════════════════════════════════════════════════════
# SOURCE 4 — CoinTelegraph RSS
# ══════════════════════════════════════════════════════════════════
def _fetch_cointelegraph():
    headlines = []
    try:
        r = requests.get(
            "https://cointelegraph.com/rss",
            timeout=8,
            headers={"User-Agent": "Mozilla/5.0 (compatible; NewsBot/1.0)"}
        )
        if r.status_code != 200:
            return headlines

        titles = re.findall(
            r"<title><!\[CDATA\[(.*?)\]\]></title>", r.text, re.DOTALL)
        if not titles:
            titles = re.findall(
                r"<title>(.*?)</title>", r.text, re.DOTALL)

        for t in titles[:15]:
            t = t.strip()
            if t and len(t) > 10:
                headlines.append(t.lower())

        if headlines:
            print(f"[CoinTelegraph] ✅ {len(headlines)} headlines")

    except Exception:
        pass   

    return headlines


# ══════════════════════════════════════════════════════════════════
# SOURCE 5 — CryptoPanic (optional)
# ══════════════════════════════════════════════════════════════════
def _fetch_cryptopanic():
    headlines = []
    token = getattr(config, "CRYPTOPANIC_TOKEN", "")
    if not token:
        return headlines
    try:
        r = requests.get(
            "https://cryptopanic.com/api/v1/posts/",
            params={"auth_token": token, "filter": "important",
                    "kind": "news", "public": "true"},
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        if r.status_code != 200:
            return headlines
        results = r.json().get("results", [])
        if isinstance(results, list):
            for item in results[:20]:
                if isinstance(item, dict):
                    t = item.get("title", "")
                    if isinstance(t, str) and len(t.strip()) > 10:
                        headlines.append(t.lower().strip())
        if headlines:
            print(f"[CryptoPanic] ✅ {len(headlines)} headlines")
    except Exception as e:
        print(f"[CryptoPanic] {e}")
    return headlines


# ══════════════════════════════════════════════════════════════════
# HEADLINE FETCHER — session-gated + cached
# ══════════════════════════════════════════════════════════════════
def _fetch_headlines():
    """
    Fetches news metrics continuously. Cache safeguards prevent API rate limits.
    """
    global _headline_cache, _headline_cache_ts, _zero_headlines_warned

    if not _in_active_session():
        return _headline_cache

    if (time.time() - _headline_cache_ts < _CACHE_TTL 
            and len(_headline_cache) > 0):
        return _headline_cache

    raw = []
    raw.extend(_fetch_cryptocompare())
    raw.extend(_fetch_cointelegraph())
    raw.extend(_fetch_cryptopanic())

    seen   = set()
    unique = []
    for h in raw:
        if h not in seen:
            seen.add(h)
            unique.append(h)

    _headline_cache    = unique
    _headline_cache_ts = time.time()

    total = len(unique)
    if total == 0 and not _zero_headlines_warned:
        _zero_headlines_warned = True
        print("[news] ⚠️  0 headlines — using calendar+F&G only.")
        print("[news]     Add CRYPTOPANIC_TOKEN in config.py for better news coverage.")
    elif total > 0:
        _zero_headlines_warned = False
        print(f"[news] ✅ {total} headlines cached")

    return _headline_cache


# ══════════════════════════════════════════════════════════════════
# HEADLINE SCANNER — tiered keyword matching
# ══════════════════════════════════════════════════════════════════
def _check_headlines():
    """
    Scans headlines in two passes:
    Pass 1: HIGH panic words → immediate stop
    Pass 2: MEDIUM panic words → caution mode
    """
    headlines = _fetch_headlines()

    # Pass 1 — HIGH
    for headline in headlines:
        if not isinstance(headline, str):
            continue
        for phrase in HIGH_PANIC_WORDS:
            if _matches(headline, phrase):
                snippet = headline[:100] + ("…" if len(headline) > 100 else "")
                return "HIGH", phrase, snippet

    # Pass 2 — MEDIUM
    for headline in headlines:
        if not isinstance(headline, str):
            continue
        for phrase in MEDIUM_PANIC_WORDS:
            if _matches(headline, phrase):
                snippet = headline[:100] + ("…" if len(headline) > 100 else "")
                return "MEDIUM", phrase, snippet

    return "LOW", "", ""


# ══════════════════════════════════════════════════════════════════
# MASTER CHECK
# ══════════════════════════════════════════════════════════════════
def check_news():
    """
    Called once per scan loop in main.py.
    HIGH   → allow=False, multiplier=0.0  (stop trading)
    MEDIUM → allow=True,  multiplier=0.5  (50% confidence)
    LOW    → allow=True,  multiplier=1.0  (normal trading)
    """
    if not getattr(config, "NEWS_FILTER_ON", True):
        return _ok()

    # 1. Check Economic Calendar
    cal_tier, cal_reason, cal_head = _check_calendar()
    if cal_tier == "HIGH":
        return _block("HIGH", cal_reason, cal_head, "calendar")

    # 2. Check Live Headlines
    news_tier, news_reason, news_head = _check_headlines()
    if news_tier == "HIGH":
        return _block("HIGH", news_reason, news_head, "headline")

    # 3. Check Fear & Greed 
    fg_tier, fg_reason, fg_head = _check_fear_greed()
    if fg_tier == "HIGH":
        return _block("HIGH", fg_reason, fg_head, "fear_greed")

    # ── MEDIUM tier mapping (Adapts and routes safely to main.py)
    if cal_tier == "MEDIUM":
        return _caution(cal_reason, cal_head, "calendar")
    if news_tier == "MEDIUM":
        return _caution(news_reason, news_head, "headline")
    if fg_tier == "MEDIUM":
        return _caution(fg_reason, fg_head, "fear_greed")

    return _ok()


# ── Result builders
def _ok():
    return {
        "allow": True, "risk_tier": "LOW",
        "confidence_multiplier": 1.0,
        "reason": "", "headline": "", "source": "",
    }

def _caution(reason, headline, source):
    return {
        "allow": True, "risk_tier": "MEDIUM",
        "confidence_multiplier": 0.5,
        "reason": reason, "headline": headline, "source": source,
    }

def _block(tier, reason, headline, source):
    return {
        "allow": False, "risk_tier": tier,
        "confidence_multiplier": 0.0,
        "reason": reason, "headline": headline, "source": source,
    }