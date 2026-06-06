# ╔══════════════════════════════════════════════════════════════╗
# ║  realtime_price.py  —  WebSocket Price Feed v6               ║
# ║                                                              ║
# ║  Streams live prices from Binance WebSocket.                 ║
# ║  Used by main.py for real-time TP/SL monitoring.             ║
# ║  Fallback: REST ticker if websocket fails.                   ║
# ╚══════════════════════════════════════════════════════════════╝

import asyncio
import json
import threading
import time
import websockets

# live price store: "btcusdt" → float
live_prices = {}

_ws_running    = False
_ws_thread     = None
_reconnect_sec = 5


def normalize(symbol):
    """BTC/USDT → btcusdt"""
    return symbol.replace("/", "").lower()


# ══════════════════════════════════════════════════════════════════
# WEBSOCKET STREAM
# ══════════════════════════════════════════════════════════════════
async def _price_stream():
    url = "wss://stream.binance.com:9443/ws/!miniTicker@arr"
    while True:
        try:
            async with websockets.connect(url, ping_interval=20) as ws:
                print("[WS] Connected to Binance stream")
                while True:
                    msg  = await ws.recv()
                    data = json.loads(msg)
                    for item in data:
                        sym   = item["s"].lower()
                        price = float(item["c"])
                        live_prices[sym] = price
        except Exception as e:
            print(f"[WS] Error: {e} — reconnecting in {_reconnect_sec}s")
            await asyncio.sleep(_reconnect_sec)


def _run_loop():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(_price_stream())


def start_socket():
    """Start WebSocket in background thread. Safe to call once."""
    global _ws_running, _ws_thread
    if _ws_running:
        return
    _ws_thread  = threading.Thread(target=_run_loop, daemon=True)
    _ws_thread.start()
    _ws_running = True
    print("[WS] Price stream started")
    time.sleep(3)   # brief warm-up


# ══════════════════════════════════════════════════════════════════
# PRICE GETTER (with REST fallback)
# ══════════════════════════════════════════════════════════════════
def get_price(symbol, exchange=None):
    """
    Returns live price for symbol.
    Primary: WebSocket cache (near-instant).
    Fallback: REST ticker (if WS not populated yet).
    """
    key = normalize(symbol)
    if key in live_prices:
        return live_prices[key]

    # REST fallback
    if exchange:
        try:
            ticker = exchange.fetch_ticker(symbol)
            return float(ticker["last"])
        except Exception as e:
            print(f"[price_fallback] {symbol}: {e}")
    return None

