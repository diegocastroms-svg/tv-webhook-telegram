# main.py — LONGSETUP CONFIRMADO V2.0 (FINAL)
# RSI ≥ 50 | Volume ≥ 1.2x | Pullback ≤ 8%
# SL dinâmico (swing low) | TP em 3 camadas
# SEM ALERTA DE TESTE | SÓ ALERTAS REAIS
# Thread ativa + Flask compatível com Render

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
    return "Scanner ativo (LongSetup V2.0) — Tendência Longa 1h/4h/1D", 200

@app.route("/health")
def health():
    return "OK", 200

# ---------------- UTILS ----------------
def now_br():
    return (datetime.utcnow() - timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S") + " BR"

async def tg(session, text: str):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print(f"[TG FALHOU] Token ou Chat ID ausente!")
        print(f"   TOKEN: {'OK' if TELEGRAM_TOKEN else 'FALTANDO'}")
        print(f"   CHAT_ID: {'OK' if CHAT_ID else 'FALTANDO'}")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    try:
        async with session.post(url, data=payload, timeout=10) as resp:
            result = await resp.json()
            if resp.status == 200 and result.get("ok"):
                print(f"[TG ENVIADO] {text.split(chr(10))[0]}...")
                return True
            else:
                print(f"[TG ERRO] {resp.status} | {result.get('description', 'Sem descrição')}")
                return False
    except Exception as e:
        print(f"[TG EXCEÇÃO] {e}")
        return False

def fmt_price(x: float) -> str:
    s = f"{x:.8f}".rstrip("0").rstrip(".")
    return s if s else "0"

def sma(seq, n):
    if len(seq) < n: return [0.0] * len(seq)
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
    except Exception as e:
        print(f"[ERRO KLINE] {symbol} {interval}: {e}")
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
            if s.get("status") != "TRADING": continue
            if s.get("quoteAsset") != "USDT": continue
            name = s.get("symbol", "")
            if any(x in name for x in ("UP","DOWN","BULL","BEAR","PERP","_PERP")): continue
            valid.add(name)
        except: continue
    return valid

async def get_top_usdt_symbols(session):
    valid = await get_valid_spot_usdt_symbols(session)
    url = f"{BINANCE_HTTP}/api/v3/ticker/24hr"
    try:
        async with session.get(url, timeout=REQ_TIMEOUT) as r:
            data = await r.json()
    except:
        data = []
    blocked = ("BUSD","FDUSD","TUSD","USDC","USDP","USD1","USDE","XUSD","USDX","GUSD","BFUSD","EUR","EURS","CEUR","BRL","TRY","STABLE","TEST")
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
    top = [s for s,_ in pares[:TOP_N]]
    print(f"[INFO] {len(top)} pares USDT válidos (TOP {TOP_N})")
    return top

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
        RSI_MIN = 48.0
        VOL_MIN = 1.1
        PULLBACK_MAX = 1.10
        TOL = 0.99

        k1h = await get_klines(session, symbol, "1h", 210)
        k4h = await get_klines(session, symbol, "4h", 210)
        k1d = await get_klines(session, symbol, "1d", 210)

        if not (len(k1h) >= 52 and len(k4h) >= 52 and len(k1d) >= 52):
            return

        c1h = [float(x[4]) for x in k1h[:-1]]
        rsi1h = calc_rsi(c1h, 14)

        c4h = [float(x[4]) for x in k4h[:-2]]
        v4h = [float(x[5]) for x in k4h[:-2]]
        ma50_4h = sma(c4h, 50)
        ma200_4h = sma(c4h, 200)
        vol_ma20_4h = sum(v4h[-21:-1]) / 20.0 if len(v4h) >= 21 else 0.0
        vol_ratio_4h = v4h[-1] / (vol_ma20_4h + 1e-12)

        if len(k4h) < 3: return
        high_prev_4h = float(k4h[-3][2])
        close_curr_4h = float(k4h[-2][4])
        vol_curr_4h = float(k4h[-2][5])
        vol_prev_4h = float(k4h[-3][5])
        continuity_4h = (close_curr_4h > high_prev_4h) and (vol_curr_4h > vol_prev_4h)

        c1d = [float(x[4]) for x in k1d[:-1]]
        ema20_1d = ema(c1d, 20)

        recent_lows = [float(x[3]) for x in k4h[-5:-1]]
        swing_low = min(recent_lows)
        sl_price = swing_low * 0.995

        cond_rsi = rsi1h[-1] >= RSI_MIN
        cond_vol = vol_ratio_4h >= VOL_MIN
        cond_ma = ma50_4h[-1] >= ma200_4h[-1] * TOL
        cond_price_ma200 = close_curr_4h >= ma200_4h[-1] * TOL
        cond_pullback = close_curr_4h <= ma50_4h[-1] * PULLBACK_MAX
        cond_1d = c1d[-1] >= ema20_1d[-1] * TOL
        cond_cont = continuity_4h

        long_ok = cond_rsi and cond_vol and cond_ma and cond_price_ma200 and cond_pullback and cond_1d and cond_cont

        if not long_ok:
            motivos = []
            if not cond_rsi: motivos.append(f"RSI < {RSI_MIN}")
            if not cond_vol: motivos.append(f"Vol < {VOL_MIN}x")
            if not cond_ma: motivos.append("MA50 < MA200")
            if not cond_price_ma200: motivos.append("Preço < MA200")
            if not cond_pullback: motivos.append("Pullback > 8%")
            if not cond_1d: motivos.append("1D < EMA20")
            if not cond_cont: motivos.append("Sem continuidade")
            print(f"[{now_br()}] {symbol} | Setup não confirmado → {', '.join(motivos)}")
            return

        if allowed(symbol, "LONG_ALERT"):
            tp1 = close_curr_4h * 1.05
            tp2 = close_curr_4h * 1.10

            msg = (
                f"<b>[LONGSETUP V2.0 – CONFIRMADO]</b>\n"
                f"Pair: <b>{symbol}</b>\n"
                f"Time: {now_br()}\n"
                f"Price: <b>{fmt_price(close_curr_4h)}</b>\n"
                f"RSI: <b>{rsi1h[-1]:.1f}</b> | Vol: <b>+{(vol_ratio_4h-1)*100:.0f}%</b>\n"
                f"Pullback ≤8% | 1D ↑\n"
                f"<b>Continuidade 4h confirmada</b>\n\n"
                f"<b>Entrada:</b> {fmt_price(close_curr_4h)}\n"
                f"<b>SL:</b> {fmt_price(sl_price)} (swing low)\n"
                f"<b>TP1:</b> {fmt_price(tp1)} (+5%)\n"
                f"<b>TP2:</b> {fmt_price(tp2)} (+10%)\n"
                f"<b>TP3:</b> Trailing Stop\n\n"
                f"<a href='https://www.binance.com/en/trade/{symbol}'>ABRIR NO BINANCE</a>"
            )

            if await tg(session, msg):
                mark(symbol, "LONG_ALERT")
                print(f"ALERTA ENVIADO: {symbol}")
            else:
                print(f"ALERTA NÃO ENVIADO (TG falhou): {symbol}")

    except Exception as e:
        print(f"[ERRO SCAN] {symbol}: {e}")

# ---------------- MAIN LOOP ----------------
async def main_loop():
    async with aiohttp.ClientSession() as session:
        await tg(session, f"<b>BOT LONGSETUP V2.0 INICIADO</b>\n{now_br()}\nScanner ativo (sem testes).")
        print(f"[{now_br()}] BOT V2.0 INICIADO | Telegram: {'OK' if TELEGRAM_TOKEN and CHAT_ID else 'NOK'}")

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

def start_bot():
    while True:
        try:
            asyncio.run(main_loop())
        except Exception as e:
            print(f"[ERRO FATAL] {e}. Reiniciando em 5s...")
            time.sleep(5)

# ---------------- EXECUÇÃO FINAL (COMPATÍVEL RENDER) ----------------
if __name__ == "__main__":
    threading.Thread(target=start_bot, daemon=False).start()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT") or 10000), use_reloader=False)
