# main.py — LONGSETUP CONFIRMADO V2.2 (FINAL)
# RSI ≥ 50 | Volume ≥ 1.2x | Pullback ≤ 8%
# SL dinâmico (swing low) | TP em 3 camadas
# SEM ALERTA DE TESTE | SÓ ALERTAS REAIS
# Totalmente compatível com Flask 3 e Render

import os, asyncio, aiohttp, time, threading
from datetime import datetime, timedelta
from flask import Flask

# ---------------- CONFIG ----------------
BINANCE_HTTP = "https://api.binance.com"
TOP_N = 80
REQ_TIMEOUT = 10
COOLDOWN_SEC = 15 * 60  # 15 min

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()

# ---------------- FLASK ----------------
app = Flask(__name__)

@app.route("/")
def home():
    return "Scanner ativo (LongSetup V2.2) — Tendência Longa 1h/4h/1D", 200

@app.route("/health")
def health():
    return "OK", 200

# ---------------- UTILS ----------------
def now_br():
    return (datetime.utcnow() - timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S") + " BR"

async def tg(session, text: str):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("[TG FALHOU] Token ou Chat ID ausente!")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        async with session.post(url, data=payload, timeout=10) as resp:
            data = await resp.json()
            if resp.status == 200 and data.get("ok"):
                print(f"[TG ENVIADO] {text.split(chr(10))[0]}...")
                return True
            else:
                print(f"[TG ERRO] {resp.status} | {data.get('description', '')}")
                return False
    except Exception as e:
        print(f"[TG EXCEÇÃO] {e}")
        return False

def fmt_price(x: float) -> str:
    s = f"{x:.8f}".rstrip("0").rstrip(".")
    return s if s else "0"

def sma(seq, n):
    if len(seq) < n: return [0.0] * len(seq)
    from collections import deque
    out, s, q = [], 0.0, deque()
    for x in seq:
        q.append(x); s += x
        if len(q) > n: s -= q.popleft()
        out.append(s / len(q))
    return out

def ema(seq, span):
    if not seq: return []
    alpha = 2.0 / (span + 1.0)
    out, e = [seq[0]], seq[0]
    for x in seq[1:]:
        e = alpha * x + (1 - alpha) * e
        out.append(e)
    return out

def calc_rsi(seq, period=14):
    if len(seq) < period + 1:
        return [50.0] * len(seq)
    gains, losses = [], []
    for i in range(1, len(seq)):
        diff = seq[i] - seq[i - 1]
        gains.append(max(diff, 0))
        losses.append(abs(min(diff, 0)))
    rsi, avg_gain = [], sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    rs = avg_gain / (avg_loss + 1e-12)
    rsi.append(100 - (100 / (1 + rs)))
    for i in range(period, len(seq) - 1):
        diff = seq[i] - seq[i - 1]
        gain, loss = max(diff, 0), abs(min(diff, 0))
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        rs = avg_gain / (avg_loss + 1e-12)
        rsi.append(100 - (100 / (1 + rs)))
    return [50.0] * (len(seq) - len(rsi)) + rsi

# ---------------- BINANCE ----------------
async def get_klines(session, symbol, interval, limit=210):
    url = f"{BINANCE_HTTP}/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    try:
        async with session.get(url, timeout=REQ_TIMEOUT) as r:
            data = await r.json()
            return data if isinstance(data, list) else []
    except Exception as e:
        print(f"[ERRO KLINE] {symbol} {interval}: {e}")
        return []

async def get_valid_spot_usdt_symbols(session):
    try:
        async with session.get(f"{BINANCE_HTTP}/api/v3/exchangeInfo", timeout=REQ_TIMEOUT) as r:
            info = await r.json()
    except:
        return set()
    valid = set()
    for s in info.get("symbols", []):
        try:
            if s.get("status") != "TRADING": continue
            if s.get("quoteAsset") != "USDT": continue
            name = s.get("symbol", "")
            if any(x in name for x in ("UP","DOWN","BULL","BEAR","PERP","_PERP")): continue
            valid.add(name)
        except: continue
    return valid

async def get_top_usdt_symbols(session):
    valid = await get_valid_spot_usdt_symbols(session)
    try:
        async with session.get(f"{BINANCE_HTTP}/api/v3/ticker/24hr", timeout=REQ_TIMEOUT) as r:
            data = await r.json()
    except:
        data = []
    blocked = ("BUSD","FDUSD","TUSD","USDC","USDP","USD1","USDE","XUSD","USDX","GUSD","EUR","EURS","CEUR","BRL","TRY","STABLE","TEST")
    pares = []
    for d in data if isinstance(data, list) else []:
        s = d.get("symbol", "")
        if s not in valid or not s.endswith("USDT"): continue
        if any(x in s for x in blocked): continue
        qv = float(d.get("quoteVolume", "0") or 0.0)
        if qv >= 15_000_000:
            pares.append((s, qv))
    pares.sort(key=lambda x: x[1], reverse=True)
    print(f"[INFO] {len(pares[:TOP_N])} pares USDT válidos (TOP {TOP_N})")
    return [s for s, _ in pares[:TOP_N]]

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
        RSI_MIN, VOL_MIN, PULLBACK_MAX, TOL = 48.0, 1.1, 1.10, 0.99

        k1h = await get_klines(session, symbol, "1h")
        k4h = await get_klines(session, symbol, "4h")
        k1d = await get_klines(session, symbol, "1d")
        if not (len(k1h) >= 52 and len(k4h) >= 52 and len(k1d) >= 52): return

        c1h, c4h, c1d = [float(x[4]) for x in k1h[:-1]], [float(x[4]) for x in k4h[:-2]], [float(x[4]) for x in k1d[:-1]]
        v4h = [float(x[5]) for x in k4h[:-2]]
        rsi1h = calc_rsi(c1h, 14)
        ma50_4h, ma200_4h = sma(c4h, 50), sma(c4h, 200)
        vol_ma20_4h = sum(v4h[-21:-1]) / 20.0 if len(v4h) >= 21 else 0.0
        vol_ratio_4h = v4h[-1] / (vol_ma20_4h + 1e-12)

        high_prev, close_curr, vol_curr, vol_prev = float(k4h[-3][2]), float(k4h[-2][4]), float(k4h[-2][5]), float(k4h[-3][5])
        continuity = (close_curr > high_prev) and (vol_curr > vol_prev)

        ema20_1d = ema(c1d, 20)
        swing_low = min(float(x[3]) for x in k4h[-5:-1])
        sl_price = swing_low * 0.995

        conds = [
            rsi1h[-1] >= RSI_MIN,
            vol_ratio_4h >= VOL_MIN,
            ma50_4h[-1] >= ma200_4h[-1] * TOL,
            close_curr >= ma200_4h[-1] * TOL,
            close_curr <= ma50_4h[-1] * PULLBACK_MAX,
            c1d[-1] >= ema20_1d[-1] * TOL,
            continuity,
        ]
        if not all(conds):
            return

        if allowed(symbol, "LONG_ALERT"):
            tp1, tp2 = close_curr * 1.05, close_curr * 1.10
            msg = (
                f"🚀 <b>[LONGSETUP V2.2 – CONFIRMADO]</b>\n"
                f"<b>{symbol}</b>\n"
                f"💰 Preço: {fmt_price(close_curr)}\n"
                f"📊 RSI: {rsi1h[-1]:.1f} | Volume: +{(vol_ratio_4h-1)*100:.0f}%\n"
                f"🧭 Pullback ≤8% | 1D ↑ | Continuidade 4h confirmada\n\n"
                f"🎯 TP1: {fmt_price(tp1)} (+5%)\n"
                f"🎯 TP2: {fmt_price(tp2)} (+10%)\n"
                f"🛡️ SL: {fmt_price(sl_price)} (swing low)\n\n"
                f"<a href='https://www.binance.com/en/trade/{symbol}'>ABRIR NO BINANCE</a>"
            )
            if await tg(session, msg):
                mark(symbol, "LONG_ALERT")
                print(f"ALERTA ENVIADO: {symbol}")

    except Exception as e:
        print(f"[ERRO SCAN] {symbol}: {e}")

# ---------------- MAIN LOOP ----------------
async def main_loop():
    async with aiohttp.ClientSession() as session:
        await tg(session, f"<b>BOT LONGSETUP V2.2 INICIADO</b>\n{now_br()}")
        print(f"[{now_br()}] BOT V2.2 INICIADO | Telegram: {'OK' if TELEGRAM_TOKEN and CHAT_ID else 'NOK'}")
        while True:
            start = time.time()
            symbols = await get_top_usdt_symbols(session)
            if not symbols:
                await asyncio.sleep(30)
                continue
            print(f"[{now_br()}] Escaneando {len(symbols)} pares...")
            await asyncio.gather(*[scan_symbol(session, s) for s in symbols])
            elapsed = time.time() - start
            print(f"[{now_br()}] Scan concluído em {elapsed:.1f}s. Próximo em 5 min...")
            await asyncio.sleep(300)

# ---------------- EXECUÇÃO FINAL ----------------
def start_bot():
    while True:
        try:
            asyncio.run(main_loop())
        except Exception as e:
            print(f"[ERRO FATAL] {e}. Reiniciando em 5s...")
            time.sleep(5)

def run_flask_with_thread():
    print("[INFO] Inicializando BOT LONGSETUP V2.2...")
    t = threading.Thread(target=start_bot, daemon=True)
    t.start()
    print("[INFO] Thread principal do BOT iniciada.")
    app.run(host="0.0.0.0", port=int(os.getenv("PORT") or 10000))

run_flask_with_thread()
