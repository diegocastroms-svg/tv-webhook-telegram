# main.py — LONGSETUP V2.4 (SÓ ALERTA LONGO - 1 DIA+)
# Entrada 15m + 4H + 1H | Stop 1h | Alvo 1:3 e 1:5 | SEM SAÍDA AUTOMÁTICA

import os, asyncio, aiohttp, time, threading
from datetime import datetime, timedelta
from flask import Flask

# ---------------- CONFIG ----------------
BINANCE_HTTP = "https://api.binance.com"
TOP_N = 80
REQ_TIMEOUT = 10
COOLDOWN_SEC = 15 * 60
VOL_MIN_USDT = 20_000_000

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()

# ---------------- FLASK ----------------
app = Flask(__name__)

@app.route("/")
def home():
    return "Scanner ativo (LongSetup V2.4) — Tendência Longa 15m/1h/4h", 200

@app.route("/health")
def health():
    return "OK", 200

# ---------------- UTILS ----------------
def now_br():
    return (datetime.utcnow() - timedelta(hours=3)).strftime("%Y-%m-%d %H:%M") + " BR"

async def tg(session, text: str):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("[TG FALHOU] Token ou Chat ID ausente!")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
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

def ema(seq, span):
    if not seq: return []
    alpha = 2.0 / (span + 1.0)
    out, e = [seq[0]], seq[0]
    for x in seq[1:]:
        e = alpha * x + (1 - alpha) * e
        out.append(e)
    return out

def calc_rsi(seq, period=14):
    if len(seq) < period + 1: return [50.0] * len(seq)
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

def macd_hist(seq):
    if len(seq) < 35: return 0.0
    ema_fast = ema(seq, 12)
    ema_slow = ema(seq, 26)
    macd_line = [f - s for f, s in zip(ema_fast, ema_slow)]
    signal = ema(macd_line, 9)
    return macd_line[-1] - signal[-1] if len(signal) > 0 else 0.0

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

async def get_top_usdt_symbols(session):
    try:
        async with session.get(f"{BINANCE_HTTP}/api/v3/ticker/24hr", timeout=REQ_TIMEOUT) as r:
            data = await r.json()
    except:
        data = []
    pares = []
    for d in data if isinstance(data, list) else []:
        s = d.get("symbol", "")
        qv = float(d.get("quoteVolume", "0") or 0.0)
        if s.endswith("USDT") and qv >= VOL_MIN_USDT:
            pares.append((s, qv))
    pares.sort(key=lambda x: x[1], reverse=True)
    only = [s for s, _ in pares[:TOP_N]]
    print(f"[{now_br()}] TOP {len(only)} pares USDT por volume")
    return only

# ---------------- ESTADO ----------------
LAST_HIT = {}

def allowed(symbol, kind, cd=COOLDOWN_SEC):
    ts = LAST_HIT.get((symbol, kind), 0.0)
    return (time.time() - ts) >= cd

def mark(symbol, kind):
    LAST_HIT[(symbol, kind)] = time.time()

# ---------------- WORKER ----------------
async def scan_symbol(session, symbol):
    try:
        # === BASE 15m (ENTRADA PRINCIPAL) ===
        k15 = await get_klines(session, symbol, "15m", 200)
        if len(k15) < 50: return
        c15 = [float(x[4]) for x in k15]
        v15 = [float(x[5]) for x in k15]
        close_15 = c15[-1]
        ema9_15 = ema(c15, 9)[-1]
        ema21_15 = ema(c15, 21)[-1]
        ema200_15 = ema(c15, 200)[-1]
        rsi_15 = calc_rsi(c15)[-1]
        macd_hist_15 = macd_hist(c15)
        vol_med_20_15 = sum(v15[-20:]) / 20
        vol_atual_15 = v15[-1]
        vol_ratio = vol_atual_15 / (vol_med_20_15 + 1e-6)

        # === CONFIRMAÇÕES 4H e 1H ===
        k4h = await get_klines(session, symbol, "4h", 100)
        if len(k4h) < 50: return
        c4h = [float(x[4]) for x in k4h]
        ema9_4h = ema(c4h, 9)[-1]
        ema21_4h = ema(c4h, 21)[-1]
        ema50_4h = ema(c4h, 50)[-1]
        macd_hist_4h = macd_hist(c4h)
        close_4h = c4h[-1]

        k1h = await get_klines(session, symbol, "1h", 100)
        if len(k1h) < 50: return
        c1h = [float(x[4]) for x in k1h]
        ema9_1h = ema(c1h, 9)[-1]
        ema21_1h = ema(c1h, 21)[-1]
        macd_hist_1h = macd_hist(c1h)
        close_1h = c1h[-1]

        # === CONDIÇÕES ===
        cond_15m = (
            ema9_15 > ema21_15 and
            close_15 > ema200_15 and
            40 <= rsi_15 <= 70 and
            macd_hist_15 > 0 and
            vol_atual_15 > vol_med_20_15 * 1.2
        )
        conf_4h = sum([ema9_4h > ema21_4h, macd_hist_4h > 0, close_4h > ema50_4h]) >= 2
        conf_1h = sum([ema9_1h > ema21_1h, macd_hist_1h > 0]) >= 2

        # === ENTRADA (SÓ ALERTA LONGO) ===
        if cond_15m and conf_4h and conf_1h and allowed(symbol, "TRIPLA"):
            swing_low = min(float(x[3]) for x in k1h[-5:])
            stop = swing_low * 0.995
            risco = close_1h - stop
            alvo_1 = close_1h + 3 * risco
            alvo_2 = close_1h + 5 * risco

            # === PROBABILIDADE E TEMPO ===
            prob = 70
            if vol_ratio > 3.0: prob += 20
            elif vol_ratio > 2.0: prob += 15
            elif vol_ratio > 1.5: prob += 10
            if rsi_15 < 50: prob += 10
            if macd_hist_15 > macd_hist(c15[-10:]): prob += 8
            prob = min(98, prob)

            if prob >= 90:
                tempo = "1-3 DIAS"
                emoji = "ROCKET"
            elif prob >= 80:
                tempo = "3-7 DIAS"
                emoji = "FIRE"
            else:
                tempo = "7-14 DIAS"
                emoji = "UP"

            # === ALERTA LONGO (SÓ ENTRADA) ===
            msg = (
                f"<b>{emoji} TENDÊNCIA LONGA CONFIRMADA</b>\n"
                f"<code>{symbol}</code>\n"
                f"Preço: <b>${fmt_price(close_1h)}</b>\n"
                f"<b>PROBABILIDADE: {prob}%</b>\n"
                f"<b>TEMPO ESTIMADO: {tempo}</b>\n"
                f"Stop: <b>${fmt_price(stop)}</b>\n"
                f"Alvo 1: <b>${fmt_price(alvo_1)}</b> (+{((alvo_1/close_1h)-1)*100:.1f}%)\n"
                f"Alvo 2: <b>${fmt_price(alvo_2)}</b> (+{((alvo_2/close_1h)-1)*100:.1f}%)\n"
                f"<i>{now_br()}</i>\n"
                f"<b>━━━━━━━━━━━━━━━</b>"
            )
            if await tg(session, msg):
                mark(symbol, "TRIPLA")
                print(f"[ALERTA LONGO] {symbol} | {prob}% | {tempo}")

    except Exception as e:
        print(f"[ERRO SCAN] {symbol}: {e}")

# ---------------- MAIN LOOP ----------------
async def main_loop():
    async with aiohttp.ClientSession() as session:
        await tg(session, f"<b>LONGSETUP V2.4 ATIVO</b>\nTendência longa (SÓ ALERTA DE ENTRADA)\n{now_br()}")
        print(f"[{now_br()}] BOT V2.4 INICIADO (SÓ ALERTA LONGO)")

        while True:
            start = time.time()
            symbols = await get_top_usdt_symbols(session)
            if not symbols:
                print(f"[{now_br()}] Nenhum par. Aguardando...")
                await asyncio.sleep(30)
                continue

            print(f"[{now_br()}] Escaneando {len(symbols)} pares...")
            await asyncio.gather(*[scan_symbol(session, s) for s in symbols])
            elapsed = time.time() - start
            print(f"[{now_br()}] Scan em {elapsed:.1f}s. Próximo em 5 min...")
            await asyncio.sleep(300)

# ---------------- EXECUÇÃO ----------------
def start_bot():
    while True:
        try:
            asyncio.run(main_loop())
        except Exception as e:
            print(f"[ERRO FATAL] {e}. Reiniciando...")
            time.sleep(5)

def run_flask_with_thread():
    print("[INFO] Iniciando LONGSETUP V2.4...")
    t = threading.Thread(target=start_bot, daemon=True)
    t.start()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT") or 10000))

run_flask_with_thread()
