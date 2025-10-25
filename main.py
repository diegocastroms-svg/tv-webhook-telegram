# main_dualsetup_v1.py
# ✅ Estrutura original preservada
# ✅ Ajustado para capturar cruzamentos e altas de dias (como VIRTUAL/USDT)
# ✅ Apenas uma notificação no início do deploy
# ✅ SWING e SMALL otimizados (RSI, volume, tendência)

import os, asyncio, aiohttp, time, math, statistics
from datetime import datetime
from flask import Flask
import threading

# ---------------- CONFIG ----------------
BINANCE_HTTP = "https://api.binance.com"
COOLDOWN_SEC = 10 * 60
TOP_N = 50
REQ_TIMEOUT = 8

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()

# ---------------- FLASK ----------------
app = Flask(__name__)

@app.route("/")
def home():
    return "✅ Scanner ativo (DualSetup) — Swing 1–3D + SmallCap 10% | 🇧🇷", 200

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
    blocked = (
        "UP", "DOWN", "BULL", "BEAR", "BUSD", "FDUSD", "TUSD", "USDC", "USD1",
        "USDE", "PERP", "_PERP", "EUR", "EURS", "CEUR", "XUSD", "USDX", "GUSD"
    )
    pares = []
    for d in data:
        s = d.get("symbol", "")
        if not s.endswith("USDT"):
            continue
        if any(x in s for x in blocked):
            continue
        try:
            qv = float(d.get("quoteVolume", "0") or 0.0)
        except:
            qv = 0.0
        pares.append((s, qv))
    pares.sort(key=lambda x: x[1], reverse=True)
    return [s for s, _ in pares[:TOP_N]]

# ---------------- ALERT STATE ----------------
LAST_HIT = {}

# ---------------- WORKER ----------------
async def scan_symbol(session, symbol):
    try:
        COOLDOWN_SWING = 20 * 60
        COOLDOWN_SMALL = 10 * 60
        def can_fire(kind: str, cd_sec: int) -> bool:
            ts = LAST_HIT.get((symbol, kind), 0.0)
            return (time.time() - ts) >= cd_sec
        def mark_fire(kind: str):
            LAST_HIT[(symbol, kind)] = time.time()

        k15 = await get_klines(session, symbol, "15m", limit=210)
        k1h = await get_klines(session, symbol, "1h", limit=210)
        k4h = await get_klines(session, symbol, "4h", limit=210)
        k1d = await get_klines(session, symbol, "1d", limit=210)
        if not (len(k15)>=50 and len(k1h)>=50 and len(k4h)>=50 and len(k1d)>=50):
            return

        # --- ARRAYS ---
        c15 = [float(k[4]) for k in k15]
        v15 = [float(k[5]) for k in k15]
        ema9_15  = ema(c15, 9)
        ema20_15 = sma(c15, 20)
        upper15, mid15, lower15 = bollinger_bands(c15, 20, 2)
        rsi15 = calc_rsi(c15, 14)
        vol_ma20_15 = sum(v15[-20:]) / 20.0 if len(v15) >= 20 else 0.0
        vol_ratio_15 = (v15[-1] / (vol_ma20_15 + 1e-12)) if vol_ma20_15 else 0.0
        bbw15 = (upper15[-1] - lower15[-1]) / (mid15[-1] + 1e-12) if mid15[-1] else 0.0
        bbw15_prev = (upper15[-2] - lower15[-2]) / (mid15[-2] + 1e-12) if mid15[-2] else bbw15
        bb_expand_15 = bbw15 > bbw15_prev

        c1h = [float(k[4]) for k in k1h]
        v1h = [float(k[5]) for k in k1h]
        ema9_1h  = ema(c1h, 9)
        ema20_1h = sma(c1h, 20)
        ma50_1h  = sma(c1h, 50)
        upper1h, mid1h, lower1h = bollinger_bands(c1h, 20, 2)
        rsi1h = calc_rsi(c1h, 14)
        vol_ma20_1h = sum(v1h[-20:]) / 20.0 if len(v1h) >= 20 else 0.0
        vol_ratio_1h = (v1h[-1] / (vol_ma20_1h + 1e-12)) if vol_ma20_1h else 0.0
        bbw1h = (upper1h[-1] - lower1h[-1]) / (mid1h[-1] + 1e-12) if mid1h[-1] else 0.0
        bbw1h_prev = (upper1h[-2] - lower1h[-2]) / (mid1h[-2] + 1e-12) if mid1h[-2] else bbw1h
        bb_expand_1h = bbw1h > bbw1h_prev

        c4h = [float(k[4]) for k in k4h]
        ema9_4h  = ema(c4h, 9)
        ema20_4h = sma(c4h, 20)

        c1d = [float(k[4]) for k in k1d]
        ema20_1d = sma(c1d, 20)

        # 🔥 SMALL CAP EXPLOSIVA
        i15 = len(c15) - 1
        i1h = len(c1h) - 1
        small_ok = (
            vol_ratio_15 >= 1.5 and
            ema9_15[i15] > ema20_15[i15] and
            60.0 <= rsi15[-1] <= 80.0 and
            bb_expand_15 and
            c1h[i1h] > ema20_1h[i1h]
        )
        if small_ok and can_fire("SMALL_ALERT", COOLDOWN_SMALL):
            price = fmt_price(c15[i15])
            msg = (
                f"🚨 <b>[EXPLOSÃO SUSTENTÁVEL DETECTADA]</b>\n"
                f"💥 {symbol}\n"
                f"🕒 {now_br()}\n"
                f"💰 Preço: {price}\n"
                f"📊 Volume: {(vol_ratio_15-1)*100:.0f}% acima da média 💣\n"
                f"📈 RSI(15m): {rsi15[-1]:.1f} | EMA9>EMA20 ✅ | BB expandindo ✅\n"
                f"⏱️ Confirmação 1h: Preço > EMA20 ✅\n"
                f"🔗 https://www.binance.com/en/trade/{symbol}"
            )
            await tg(session, msg)
            mark_fire("SMALL_ALERT")

        # 🟩 SWING CURTO – agora detecta cruzamento e tendência em andamento
        i1 = len(c1h) - 1
        trend_up_1h = ema9_1h[-1] > ema20_1h[-1]  # menos restritivo
        swing_ok = (
            trend_up_1h and
            rsi1h[-1] > 50.0 and
            vol_ratio_1h >= 1.0 and
            bb_expand_1h and
            ema9_4h[-1] > ema20_4h[-1] and
            c1d[-1] > ema20_1d[-1]
        )
        if swing_ok and can_fire("SWING_ALERT", COOLDOWN_SWING):
            price = fmt_price(c1h[i1])
            msg = (
                f"💹 <b>[SWING CURTO – TENDÊNCIA SUSTENTADA]</b>\n"
                f"📊 {symbol}\n"
                f"🕒 {now_br()}\n"
                f"💰 Preço: {price}\n"
                f"📈 EMA9>EMA20 (1h/4h) ✅ | RSI: {rsi1h[-1]:.1f} | Volume ok ✅ | BB abrindo ✅\n"
                f"🧭 Direção 1D: Close > EMA20 ✅\n"
                f"🔗 https://www.binance.com/en/trade/{symbol}"
            )
            await tg(session, msg)
            mark_fire("SWING_ALERT")

    except:
        return

# ---------------- MAIN LOOP ----------------
async def main_loop():
    async with aiohttp.ClientSession() as session:
        print("BOT DUALSETUP INICIADO ✅", flush=True)
        await tg(session, f"✅ BOT DUALSETUP INICIADO COM SUCESSO 🚀\n🕒 {now_br()}\n🔍 Scanner DualSetup ativo e operando 🇧🇷")
        symbols = await get_top_usdt_symbols(session)
        while True:
            try:
                tasks = [scan_symbol(session, s) for s in symbols]
                await asyncio.gather(*tasks)
                await asyncio.sleep(10)
            except Exception as e:
                print(f"[ERRO CICLO] {e}", flush=True)
                await asyncio.sleep(10)
                continue

# ---------------- RUN ----------------
def start_bot():
    asyncio.run(main_loop())

threading.Thread(target=start_bot, daemon=True).start()
app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10001)))
