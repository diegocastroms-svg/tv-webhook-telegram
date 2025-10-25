# main_dualsetup_v2.py
# ✅ Estrutura original preservada (Flask + thread + asyncio.run + utils)
# ✅ Faixas flexíveis aplicadas aos setups
# ✅ Mensagem de inicialização mantida
# ✅ Correção final: thread inicia antes do Flask (Render fix)

import os, asyncio, aiohttp, time, math, statistics, threading
from datetime import datetime
from flask import Flask

# ---------------- CONFIG ----------------
BINANCE_HTTP = "https://api.binance.com"
TOP_N = 50
REQ_TIMEOUT = 8

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()

# ---------------- FLASK ----------------
app = Flask(__name__)

@app.route("/")
def home():
    return "✅ Scanner ativo (DualSetup Flex) — Swing + SmallCap | 🇧🇷", 200

# ---------------- UTILS ----------------
def now_br():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S") + " 🇧🇷"

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
    out = [seq[0]]
    e = seq[0]
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
            if isinstance(data, list):
                return data
            return []
    except:
        return []

async def get_top_usdt_symbols(session):
    url = f"{BINANCE_HTTP}/api/v3/ticker/24hr"
    async with session.get(url, timeout=REQ_TIMEOUT) as r:
        data = await r.json()
    blocked = ("UP","DOWN","BULL","BEAR","BUSD","FDUSD","TUSD","USDC","USD1",
               "USDE","PERP","_PERP","EUR","EURS","CEUR","XUSD","USDX","GUSD")
    pares = []
    for d in data:
        s = d.get("symbol","")
        if not s.endswith("USDT"): continue
        if any(x in s for x in blocked): continue
        try:
            qv = float(d.get("quoteVolume","0") or 0.0)
        except:
            qv = 0.0
        pares.append((s,qv))
    pares.sort(key=lambda x:x[1], reverse=True)
    return [s for s,_ in pares[:TOP_N]]

# ---------------- ALERT STATE ----------------
LAST_HIT = {}

def allowed(symbol, kind, cooldown):
    ts = LAST_HIT.get((symbol, kind), 0.0)
    return (time.time() - ts) >= cooldown

def mark(symbol, kind):
    LAST_HIT[(symbol, kind)] = time.time()

# ---------------- WORKER ----------------
async def scan_symbol(session, symbol):
    try:
        # --- cooldowns ---
        CD_SWING = 10*60
        CD_SMALL = 8*60

        k15 = await get_klines(session, symbol, "15m")
        k1h = await get_klines(session, symbol, "1h")
        k4h = await get_klines(session, symbol, "4h")
        k1d = await get_klines(session, symbol, "1d")
        if not (len(k15)>50 and len(k1h)>50 and len(k4h)>50 and len(k1d)>50):
            return

        # --- arrays ---
        c15=[float(k[4]) for k in k15]; v15=[float(k[5]) for k in k15]
        c1h=[float(k[4]) for k in k1h]; v1h=[float(k[5]) for k in k1h]
        c4h=[float(k[4]) for k in k4h]; c1d=[float(k[4]) for k in k1d]

        ema9_15=ema(c15,9); ema20_15=sma(c15,20)
        ema9_1h=ema(c1h,9); ema20_1h=sma(c1h,20)
        ma50_1h=sma(c1h,50); ma200_1h=sma(c1h,200)
        ema9_4h=ema(c4h,9); ema20_4h=sma(c4h,20)
        ma50_4h=sma(c4h,50); ma200_4h=sma(c4h,200)
        ema20_1d=sma(c1d,20)

        upper15,mid15,lower15=bollinger_bands(c15,20,2)
        upper1h,mid1h,lower1h=bollinger_bands(c1h,20,2)
        rsi15=calc_rsi(c15,14); rsi1h=calc_rsi(c1h,14)

        vol_ma20_15=sum(v15[-20:])/20; vol_ratio_15=v15[-1]/(vol_ma20_15+1e-12)
        vol_ma20_1h=sum(v1h[-20:])/20; vol_ratio_1h=v1h[-1]/(vol_ma20_1h+1e-12)

        bbw15=(upper15[-1]-lower15[-1])/(mid15[-1]+1e-12)
        bbw15_prev=(upper15[-2]-lower15[-2])/(mid15[-2]+1e-12)
        bbw1h=(upper1h[-1]-lower1h[-1])/(mid1h[-1]+1e-12)
        bbw1h_prev=(upper1h[-2]-lower1h[-2])/(mid1h[-2]+1e-12)

        # ---------------- SMALL CAP FLEX ----------------
        small_ok = (
            55 <= rsi15[-1] <= 80 and
            1.3 <= vol_ratio_15 <= 6.0 and
            ema9_15[-1] > ema20_15[-1] and
            bbw15 >= 0.98*bbw15_prev and
            c1h[-1] > ema20_1h[-1]
        )
        if small_ok and allowed(symbol,"SMALL",CD_SMALL):
            msg = (
                f"🔥 <b>[SMALL CAP EXPLOSIVA FLEX]</b>\n"
                f"💥 {symbol}\n"
                f"🕒 {now_br()}\n"
                f"📈 RSI15m {rsi15[-1]:.1f} | Volume {vol_ratio_15:.2f}× | BB abrindo ✅\n"
                f"🔗 https://www.binance.com/en/trade/{symbol}"
            )
            await tg(session,msg)
            mark(symbol,"SMALL")

        # ---------------- SWING FLEX ----------------
        cross_9_20 = ema9_1h[-2] <= ema20_1h[-2] and ema9_1h[-1] > ema20_1h[-1]
        swing_ok = (
            cross_9_20 and
            45 <= rsi1h[-1] <= 60 and
            0.8 <= vol_ratio_1h <= 3.0 and
            bbw1h >= 0.98*bbw1h_prev and
            ema9_4h[-1] > ema20_4h[-1] and
            ma50_4h[-1] > ma200_4h[-1] and
            c1d[-1] > ema20_1d[-1]
        )
        if swing_ok and allowed(symbol,"SWING",CD_SWING):
            msg = (
                f"💹 <b>[SWING CURTO FLEX]</b>\n"
                f"📊 {symbol}\n"
                f"🕒 {now_br()}\n"
                f"📈 RSI(1h): {rsi1h[-1]:.1f} | Volume: {vol_ratio_1h:.2f}× | BB abrindo ✅\n"
                f"🔗 https://www.binance.com/en/trade/{symbol}"
            )
            await tg(session,msg)
            mark(symbol,"SWING")
    except:
        return

# ---------------- MAIN LOOP ----------------
async def main_loop():
    async with aiohttp.ClientSession() as session:
        print("BOT DUALSETUP INICIADO ✅", flush=True)
        symbols = await get_top_usdt_symbols(session)
        await tg(session, f"✅ BOT DUALSETUP INICIADO COM SUCESSO 🚀 | {len(symbols)} pares | {now_br()}")
        if not symbols: return
        while True:
            await asyncio.gather(*[scan_symbol(session,s) for s in symbols])
            await asyncio.sleep(10)

def start_bot():
    while True:
        try:
            asyncio.run(main_loop())
        except Exception:
            time.sleep(5)

# ---------------- RUN ----------------
if __name__ == "__main__":
    print("Iniciando DualSetup Flex...", flush=True)
    threading.Thread(target=start_bot, daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 50000)))
