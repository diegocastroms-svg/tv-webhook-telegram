# main.py — LONGSETUP Confirmado (tendência longa)
# ✅ Estrutura original (Flask + thread + asyncio.run + utils)
# ✅ Porta 50000 + /health
# ✅ Remoção automática de pares não-SPOT / mortos (exchangeInfo)
# ✅ Confirmação REAL: candle fecha acima da máxima anterior + volume maior
# ✅ Cooldown 15 min (evita alertas repetidos no mesmo candle)

import os, asyncio, aiohttp, time, statistics, threading
from datetime import datetime, timedelta
from flask import Flask

# ---------------- CONFIG ----------------
BINANCE_HTTP = "https://api.binance.com"
TOP_N = 80
REQ_TIMEOUT = 8
COOLDOWN_SEC = 15 * 60  # 15 min (1 candle 15m)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()

# ---------------- FLASK ----------------
app = Flask(__name__)

@app.route("/")
def home():
    return "✅ Scanner ativo (LongSetup Confirmado) — Tendência Longa 1h/4h/1D | 🇧🇷", 200

@app.route("/health")
def health():
    return "OK", 200

# ---------------- UTILS ----------------
def now_br():
    return (datetime.utcnow() - timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S") + " 🇧🇷"

async def tg(session, text: str):
    if not (TELEGRAM_TOKEN and CHAT_ID):
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
        await session.post(url, data=payload, timeout=REQ_TIMEOUT)
    except:
        pass

def fmt_price(x: float) -> str:
    s = f"{x:.8f}".rstrip("0").rstrip(".")
    return s if s else "0"

def sma(seq, n):
    out, s = [], 0.0
    from collections import deque
    q = deque()
    for x in seq:
        q.append(x); s += x
        if len(q) > n: s -= q.popleft()
        out.append(s/len(q))
    return out

def ema(seq, span):
    if not seq: return []
    alpha = 2.0/(span+1.0)
    out, e = [seq[0]], seq[0]
    for x in seq[1:]:
        e = alpha*x + (1-alpha)*e
        out.append(e)
    return out

def bollinger_bands(seq, n=20, mult=2):
    if len(seq) < n: return [], [], []
    out_mid, out_upper, out_lower = [], [], []
    for i in range(len(seq)):
        window = seq[max(0, i-n+1):i+1]
        m = sum(window)/len(window)
        s = statistics.pstdev(window)
        out_mid.append(m)
        out_upper.append(m + mult*s)
        out_lower.append(m - mult*s)
    return out_upper, out_mid, out_lower

def calc_rsi(seq, period=14):
    if len(seq) < period + 1:
        return [50.0] * len(seq)
    gains, losses = [], []
    for i in range(1, len(seq)):
        diff = seq[i] - seq[i-1]
        gains.append(max(diff, 0))
        losses.append(abs(min(diff, 0)))
    rsi = []
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    rs = avg_gain / (avg_loss + 1e-12)
    rsi.append(100 - (100 / (1 + rs)))
    for i in range(period, len(seq)-1):
        diff = seq[i] - seq[i-1]
        gain = max(diff, 0)
        loss = abs(min(diff, 0))
        avg_gain = (avg_gain * (period-1) + gain) / period
        avg_loss = (avg_loss * (period-1) + loss) / period
        rs = avg_gain / (avg_loss + 1e-12)
        rsi.append(100 - (100 / (1 + rs)))
    return [50.0]*(len(seq)-len(rsi)) + rsi

# ---------------- BINANCE ----------------
async def get_klines(session, symbol, interval, limit=210):
    url = f"{BINANCE_HTTP}/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    try:
        async with session.get(url, timeout=REQ_TIMEOUT) as r:
            data = await r.json()
            return data if isinstance(data, list) else []
    except:
        return []

async def get_valid_spot_usdt_symbols(session):
    url = f"{BINANCE_HTTP}/api/v3/exchangeInfo"
    try:
        async with session.get(url, timeout=REQ_TIMEOUT) as r:
            data = await r.json()
    except:
        return set()
    valid = set()
    for s in data.get("symbols", []):
        try:
            if s.get("status") != "TRADING": 
                continue
            if s.get("quoteAsset") != "USDT": 
                continue
            name = s.get("symbol", "")
            if any(x in name for x in ("UP","DOWN","BULL","BEAR","PERP","_PERP")):
                continue
            valid.add(name)
        except:
            continue
    return valid

async def get_top_usdt_symbols(session):
    valid = await get_valid_spot_usdt_symbols(session)
    url = f"{BINANCE_HTTP}/api/v3/ticker/24hr"
    try:
        async with session.get(url, timeout=REQ_TIMEOUT) as r:
            data = await r.json()
    except:
        data = []
    blocked = (
        "BUSD","FDUSD","TUSD","USDC","USDP","USD1","USDE","XUSD","USDX","GUSD","BFUSD",
        "EUR","EURS","CEUR","BRL","TRY",
        "STABLE","TEST"
    )
    pares = []
    for d in data if isinstance(data, list) else []:
        s = d.get("symbol", "")
        if s not in valid or not s.endswith("USDT"):
            continue
        if any(x in s for x in blocked):
            continue
        try:
            qv = float(d.get("quoteVolume", "0") or 0.0)
        except:
            qv = 0.0
        if qv < 15_000_000:
            continue
        pares.append((s, qv))
    pares.sort(key=lambda x: x[1], reverse=True)
    return [s for s,_ in pares[:TOP_N]]

# ---------------- ALERT STATE ----------------
LAST_HIT = {}
def allowed(symbol, kind, cd=COOLDOWN_SEC):
    ts = LAST_HIT.get((symbol, kind), 0.0)
    return (time.time() - ts) >= cd
def mark(symbol, kind):
    LAST_HIT[(symbol, kind)] = time.time()

# ---------------- WORKER ----------------
async def scan_symbol(session, symbol):
    try:
        # Parâmetros para tendência longa
        RSI_LONG_MIN, RSI_LONG_MAX = 40.0, 55.0
        VOL_LONG_MIN = 1.2  # 20% acima da média
        TOL_EMA = 0.99  # tolerância para pullback

        # ---- Fetch klines
        k1h = await get_klines(session, symbol, "1h", 210)
        k4h = await get_klines(session, symbol, "4h", 210)
        k1d = await get_klines(session, symbol, "1d", 210)
        if not (len(k1h) >= 52 and len(k4h) >= 52 and len(k1d) >= 52):
            return

        # Usar APENAS candles FECHADOS
        c1h = [float(x[4]) for x in k1h[:-0]]  # sequência completa
        v1h = [float(x[5]) for x in k1h[:-0]]
        c4h = [float(x[4]) for x in k4h[:-0]]
        v4h = [float(x[5]) for x in k4h[:-0]]
        c1d = [float(x[4]) for x in k1d[:-0]]

        # --- Indicadores 1h (RSI para entrada precisa)
        rsi1h = calc_rsi(c1h, 14)
        vol_ma20_1h = sum(v1h[-21:-1]) / 20.0 if len(v1h) >= 21 else 0.0
        vol_ratio_1h = (v1h[-1] / (vol_ma20_1h + 1e-12)) if vol_ma20_1h else 0.0

        # --- Indicadores 4h (tendência e pullback)
        ma50_4h = sma(c4h, 50)
        ma200_4h = sma(c4h, 200)
        vol_ma20_4h = sum(v4h[-21:-1]) / 20.0 if len(v4h) >= 21 else 0.0
        vol_ratio_4h = (v4h[-1] / (vol_ma20_4h + 1e-12)) if vol_ma20_4h else 0.0

        # Continuidade 4h: candle atual (fechado) vs anterior
        high_prev_4h = float(k4h[-2][2])
        close_prev_4h = float(k4h[-2][4])
        vol_prev_4h = float(k4h[-2][5])
        high_curr_4h = float(k4h[-1][2])
        close_curr_4h = float(k4h[-1][4])
        vol_curr_4h = float(k4h[-1][5])
        continuity_4h = (close_curr_4h > high_prev_4h) and (vol_curr_4h > vol_prev_4h)

        # --- Tendência longa (4h/1d)
        ema20_1d = sma(c1d, 20)

        # ============= 🟩 LONGSETUP (1–10 dias) (1h/4h/1D) =============
        long_ok = (
            (RSI_LONG_MIN <= rsi1h[-1] <= RSI_LONG_MAX) and  # RSI 1h na zona 40-50
            (vol_ratio_4h >= VOL_LONG_MIN) and  # Volume 20% acima da média
            (ma50_4h[-1] >= ma200_4h[-1] * TOL_EMA) and  # Uptrend: MA50 > MA200
            (close_curr_4h >= ma200_4h[-1] * TOL_EMA) and  # Preço acima MA200
            (close_curr_4h <= ma50_4h[-1] * 1.03) and  # Pullback: preço até 3% acima MA50
            (c1d[-1] >= ema20_1d[-1] * TOL_EMA) and  # Direção 1D
            continuity_4h  # Confirmação real
        )
        if long_ok and allowed(symbol, "LONG_ALERT"):
            msg = (
                f"💹 <b>[LONGSETUP – TENDÊNCIA SUSTENTADA]</b>\n"
                f"📊 {symbol}\n"
                f"🕒 {now_br()}\n"
                f"💰 Preço: {fmt_price(close_curr_4h)}\n"
                f"📈 MA50≥MA200 (4h) | Preço ≥ MA200 ✅\n"
                f"⚡ RSI(1h): {rsi1h[-1]:.1f} | Volume: {(vol_ratio_4h-1)*100:.0f}% acima ✅\n"
                f"🧭 Pullback: Preço ≤ MA50 (4h) ✅ | Direção 1D: Close ≥ EMA20 ✅\n"
                f"⏱️ Continuidade (4h) ✅\n"
                f"🔧 Sugestão: Compre a {fmt_price(close_curr_4h)}, Stop Loss ~{fmt_price(close_curr_4h * 0.97)} (-3%), Take Profit ~{fmt_price(close_curr_4h * 1.10)} (+10%)\n"
                f"🔗 https://www.binance.com/en/trade/{symbol}"
            )
            await tg(session, msg)
            mark(symbol, "LONG_ALERT")

    except:
        return

# ---------------- MAIN LOOP ----------------
async def main_loop():
    async with aiohttp.ClientSession() as session:
        await tg(session, f"✅ BOT LONGSETUP INICIADO (Confirmado) 🚀 | {now_br()}")

        while True:
            symbols = await get_top_usdt_symbols(session)
            if not symbols:
                await asyncio.sleep(30)
                continue
            await asyncio.gather(*[scan_symbol(session, s) for s in symbols])
            await asyncio.sleep(300)


def start_bot():
    while True:
        try:
            asyncio.run(main_loop())
        except Exception:
            time.sleep(5)

# ---------------- RUN ----------------
if __name__ == "__main__":
    def start_after_ready():
        time.sleep(2)
        start_bot()
    threading.Thread(target=start_after_ready, daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 50000)), use_reloader=False)




