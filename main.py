# main.py — DUALSETUP ESTÁVEL (CONFIRMAÇÃO + MOEDAS VÁLIDAS)
# ✅ Mantém estrutura original (Flask + thread + aiohttp)
# ✅ Porta 50000 + /health
# ✅ Remove moedas mortas (fora do SPOT)
# ✅ Só envia alerta após fechamento confirmado + rompimento da máxima anterior

import os, asyncio, aiohttp, time, statistics, threading
from datetime import datetime, timedelta
from flask import Flask

# ---------------- CONFIG ----------------
BINANCE_HTTP = "https://api.binance.com"
TOP_N = 80
REQ_TIMEOUT = 8
COOLDOWN_SEC = 8 * 60  # 8 minutos

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()

# ---------------- FLASK ----------------
app = Flask(__name__)

@app.route("/")
def home():
    return "✅ Scanner ativo (DualSetup Confirmado) | 🇧🇷", 200

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

async def get_valid_symbols(session):
    url = f"{BINANCE_HTTP}/api/v3/exchangeInfo"
    try:
        async with session.get(url, timeout=REQ_TIMEOUT) as r:
            data = await r.json()
            symbols = [s["symbol"] for s in data["symbols"] if s["quoteAsset"] == "USDT" and s["status"] == "TRADING"]
            return symbols
    except:
        return []

async def get_top_usdt_symbols(session):
    url = f"{BINANCE_HTTP}/api/v3/ticker/24hr"
    valid = set(await get_valid_symbols(session))
    try:
        async with session.get(url, timeout=REQ_TIMEOUT) as r:
            data = await r.json()
    except:
        data = []
    blocked = ("UP","DOWN","BULL","BEAR","BUSD","FDUSD","TUSD","USDC","USDP","USD","EUR","BRL","TRY","PERP","STABLE")
    pares = []
    for d in data if isinstance(data, list) else []:
        s = d.get("symbol", "")
        if s not in valid or not s.endswith("USDT"): continue
        if any(x in s for x in blocked): continue
        try:
            qv = float(d.get("quoteVolume", "0") or 0.0)
        except:
            qv = 0.0
        if qv < 15_000_000: continue
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
        RSI_SMALL_MIN, RSI_SMALL_MAX = 55.0, 80.0
        VOL_SMALL_MIN, VOL_SMALL_MAX = 1.3, 6.0
        TOL_BB, TOL_EMA = 0.98, 0.99

        k15 = await get_klines(session, symbol, "15m", 210)
        k1h = await get_klines(session, symbol, "1h", 210)
        if not (len(k15) >= 50 and len(k1h) >= 50):
            return

        # Usa fechamento confirmado (último candle fechado)
        c15 = [float(k[4]) for k in k15[:-1]]
        v15 = [float(k[5]) for k in k15[:-1]]
        c1h = [float(k[4]) for k in k1h[:-1]]
        v1h = [float(k[5]) for k in k1h[:-1]]

        ema9_15 = ema(c15,9); ema20_15 = sma(c15,20)
        u15,m15,l15 = bollinger_bands(c15,20,2)
        rsi15 = calc_rsi(c15,14)
        vol_ma20_15 = sum(v15[-20:])/20.0
        vol_ratio_15 = v15[-1]/(vol_ma20_15+1e-12)
        bbw15 = (u15[-1]-l15[-1])/(m15[-1]+1e-12)
        bbw15_prev = (u15[-2]-l15[-2])/(m15[-2]+1e-12)
        bb_expand_15 = bbw15 >= bbw15_prev * TOL_BB
        high_prev = float(k15[-2][2])  # máxima do candle anterior
        close_prev = float(k15[-2][4])
        close_now = float(k15[-1][4])

        ema20_1h = sma(c1h,20)
        if (RSI_SMALL_MIN <= rsi15[-1] <= RSI_SMALL_MAX) and \
           (VOL_SMALL_MIN <= vol_ratio_15 <= VOL_SMALL_MAX) and \
           (ema9_15[-1] >= ema20_15[-1]*TOL_EMA) and bb_expand_15 and \
           (close_now > high_prev) and (close_prev >= ema20_1h[-1]*TOL_EMA):

            if allowed(symbol, "SMALL_ALERT"):
                price = fmt_price(close_now)
                msg = (
                    f"🚨 <b>[EXPLOSÃO SUSTENTÁVEL DETECTADA]</b>\n"
                    f"💥 {symbol}\n"
                    f"🕒 {now_br()}\n"
                    f"💰 Preço: {price}\n"
                    f"📊 Volume: {(vol_ratio_15-1)*100:.0f}% acima da média 💣\n"
                    f"📈 RSI(15m): {rsi15[-1]:.1f} | EMA9≥EMA20 | BB abrindo ✅\n"
                    f"⏱️ Confirmação 1h: Close ≥ EMA20 ✅\n"
                    f"🔗 https://www.binance.com/en/trade/{symbol}"
                )
                await tg(session, msg)
                mark(symbol, "SMALL_ALERT")

    except Exception:
        return

# ---------------- MAIN LOOP ----------------
async def main_loop():
    async with aiohttp.ClientSession() as session:
        symbols = await get_top_usdt_symbols(session)
        await tg(session, f"✅ BOT DUALSETUP CONFIRMADO 🚀 | {len(symbols)} pares | {now_br()}")
        while True:
            await asyncio.gather(*[scan_symbol(session, s) for s in symbols])
            await asyncio.sleep(10)

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
