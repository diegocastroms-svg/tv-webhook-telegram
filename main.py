# main_long_v1.py — TENDÊNCIA LONGA OURO
# 15m e 30m: EMA9 cruza acima da EMA20 + RSI 45–70
# 1h e 4h: MACD hist > 0
# Volume 30m (USDT) ≥ 30M
# Cooldown 30 min
# Alerta com moldura, tudo em negrito, alvos em %
# Taxa de acerto dinâmica (últimas 50 operações)

import os, asyncio, aiohttp, time, threading
from datetime import datetime, timedelta
from flask import Flask

# ---------------- CONFIG ----------------
BINANCE_HTTP = "https://api.binance.com"
TOP_N = 80
REQ_TIMEOUT = 10
COOLDOWN_SEC = 30 * 60  # 30 min
VOL_30M_MIN_USDT = 30_000_000
WINRATE_WINDOW = 50

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()

# ---------------- FLASK ----------------
app = Flask(__name__)

@app.route("/")
def home():
    return "Scanner ativo — TENDÊNCIA LONGA OURO (15m/30m cruzamento + RSI | 1h/4h MACD)", 200

@app.route("/health")
def health():
    return "OK", 200

# ---------------- UTILS ----------------
def now_br():
    return (datetime.utcnow() - timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S") + " BR"

async def tg(session, text: str):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("[TG] (simulado) " + text.replace("\n", " ")[:160])
        return True
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        async with session.post(url, data=payload, timeout=REQ_TIMEOUT) as resp:
            ok = (resp.status == 200)
            if not ok:
                print(f"[TG ERRO] status={resp.status}")
            return ok
    except Exception as e:
        print(f"[TG EXCEÇÃO] {e}")
        return False

def fmt_price(x: float) -> str:
    s = f"{x:.8f}".rstrip("0").rstrip(".")
    return s if s else "0"

def pct(a, b) -> float:
    if b == 0: return 0.0
    return (a/b - 1.0) * 100.0

def ema(seq, span):
    if not seq: return []
    alpha = 2.0 / (span + 1.0)
    out, e = [seq[0]], seq[0]
    for x in seq[1:]:
        e = alpha * x + (1 - alpha) * e
        out.append(e)
    return out

def macd_hist_series(seq, fast=12, slow=26, signal=9):
    if len(seq) < slow + signal + 1:
        return [0.0] * len(seq)
    ef, es = ema(seq, fast), ema(seq, slow)
    macd_line = [f - s for f, s in zip(ef, es)]
    sig = ema(macd_line, signal)
    m = len(macd_line); s = len(sig)
    if s < m:
        sig = [sig[0]] * (m - s) + sig
    return [m_ - s_ for m_, s_ in zip(macd_line, sig)]

def calc_rsi(seq, period=14):
    n = len(seq)
    if n < period + 1: return [50.0]*n
    gains, losses = [], []
    for i in range(1, n):
        d = seq[i] - seq[i-1]
        gains.append(max(d, 0))
        losses.append(abs(min(d, 0)))
    rsis = []
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    rs = avg_gain / (avg_loss + 1e-12)
    rsis.append(100 - (100/(1+rs)))
    for i in range(period, n-1):
        d = seq[i] - seq[i-1]
        g = max(d, 0)
        l = abs(min(d, 0))
        avg_gain = (avg_gain*(period-1) + g) / period
        avg_loss = (avg_loss*(period-1) + l) / period
        rs = avg_gain / (avg_loss + 1e-12)
        rsis.append(100 - (100/(1+rs)))
    return [50.0]*(n-len(rsis)) + rsis

def cruzou_up(e9_prev, e20_prev, e9_now, e20_now) -> bool:
    return e9_prev <= e20_prev and e9_now > e20_now

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

# ---------------- ESTADO ----------------
LAST_HIT = {}               # cooldown por símbolo
POSITIONS = {}              # {symbol: {"entry","stop","alvo1","alvo2","active":bool}}
RESULTS = []                # lista de bools (True=win, False=loss), últimas N operações

def allowed(symbol, cd=COOLDOWN_SEC):
    ts = LAST_HIT.get(symbol, 0.0)
    return (time.time() - ts) >= cd

def mark(symbol):
    LAST_HIT[symbol] = time.time()

def winrate() -> float:
    if not RESULTS: return 0.0
    base = RESULTS[-WINRATE_WINDOW:]
    return (sum(1 for r in base if r) / len(base)) * 100.0

def push_result(ok: bool):
    RESULTS.append(ok)
    if len(RESULTS) > 1000:
        del RESULTS[:len(RESULTS)-1000]

# ---------------- WORKER ----------------
async def scan_symbol(session, symbol):
    try:
        # 15m, 30m, 1h, 4h
        k15 = await get_klines(session, symbol, "15m", 120)
        k30 = await get_klines(session, symbol, "30m", 120)
        k1h = await get_klines(session, symbol, "1h", 120)
        k4h = await get_klines(session, symbol, "4h", 120)
        if not (k15 and k30 and k1h and k4h): return

        # ---- SERIES ----
        c15 = [float(x[4]) for x in k15]
        c30 = [float(x[4]) for x in k30]
        c1h = [float(x[4]) for x in k1h]
        c4h = [float(x[4]) for x in k4h]
        l30 = [float(x[3]) for x in k30]
        # quote volume 30m (kline col 7)
        qv30_last = float(k30[-1][7]) if len(k30[-1]) > 7 else 0.0

        # ---- INDICADORES (fechados) ----
        # 15m cruzamento + RSI
        e9_15, e20_15 = ema(c15, 9), ema(c15, 20)
        cruz_15 = len(e9_15) >= 2 and len(e20_15) >= 2 and cruzou_up(e9_15[-2], e20_15[-2], e9_15[-1], e20_15[-1])
        rsi15 = calc_rsi(c15, 14)[-1] if len(c15) else 50.0

        # 30m cruzamento + RSI
        e9_30, e20_30 = ema(c30, 9), ema(c30, 20)
        cruz_30 = len(e9_30) >= 2 and len(e20_30) >= 2 and cruzou_up(e9_30[-2], e20_30[-2], e9_30[-1], e20_30[-1])
        rsi30 = calc_rsi(c30, 14)[-1] if len(c30) else 50.0

        # 1h e 4h: MACD verde
        h1h = macd_hist_series(c1h)
        h4h = macd_hist_series(c4h)
        macd1h_pos = (len(h1h) > 0 and h1h[-1] > 0)
        macd4h_pos = (len(h4h) > 0 and h4h[-1] > 0)

        # ---- REGRAS DO ALERTA LONGO ----
        cond = (
            cruz_15 and 45 <= rsi15 <= 70 and
            cruz_30 and 45 <= rsi30 <= 70 and
            macd1h_pos and
            macd4h_pos and
            qv30_last >= VOL_30M_MIN_USDT
        )

        # ---- RESOLUÇÃO DE POSIÇÕES ABERTAS (ganho/perda) ----
        # Usa a vela mais recente de 30m para avaliar stop/alvos
        if symbol in POSITIONS and POSITIONS[symbol]["active"]:
            entry = POSITIONS[symbol]["entry"]
            stop = POSITIONS[symbol]["stop"]
            alvo1 = POSITIONS[symbol]["alvo1"]
            # preços da última vela de 30m
            high30 = float(k30[-1][2]); low30 = float(k30[-1][3]); close30 = float(k30[-1][4])
            # Verifica primeiro alvo1, depois stop
            if high30 >= alvo1:
                push_result(True)
                POSITIONS[symbol]["active"] = False
            elif low30 <= stop:
                push_result(False)
                POSITIONS[symbol]["active"] = False
            # (alvo2 é apenas informativo para take total)

        # ---- DISPARO DO ALERTA ----
        if cond and allowed(symbol, COOLDOWN_SEC):
            preco = float(k30[-1][4])
            # stop: proteção sob mínima da vela anterior e EMA21(30m)
            ema21_30 = ema(c30, 21)[-1] if len(c30) >= 21 else c30[-1]
            stop = min(l30[-2], ema21_30) if len(l30) >= 2 else ema21_30
            risco = max(preco - stop, 1e-12)
            alvo1 = preco + 2.5 * risco
            alvo2 = preco + 5.0 * risco

            POSITIONS[symbol] = {"entry": preco, "stop": stop, "alvo1": alvo1, "alvo2": alvo2, "active": True}

            liq_text = f"US$ {qv30_last/1_000_000:.1f}M"
            acerto = winrate()

            msg = (
                "──────────────────────────────\n"
                f"🟩📈 <b>TENDÊNCIA LONGA CONFIRMADA</b>\n"
                f"<b>{symbol}</b>\n"
                f"<b>15m✅ 30m✅ 1h✅ 4h✅</b>\n"
                f"<b>RSI15: {rsi15:.1f} | RSI30: {rsi30:.1f}</b>\n"
                f"<b>Liquidez (30m): {liq_text}</b>\n"
                f"<b>Taxa de acerto (últ. {min(len(RESULTS), WINRATE_WINDOW) or 0}): {acerto:.1f}% 📊</b>\n\n"
                f"<b>💰 Preço:</b> {fmt_price(preco)}\n"
                f"<b>🛡️ Stop:</b> {fmt_price(stop)}\n"
                f"<b>🎯 Alvo1:</b> {fmt_price(alvo1)} (+{pct(alvo1, preco):.1f}%)\n"
                f"<b>🎯 Alvo2:</b> {fmt_price(alvo2)} (+{pct(alvo2, preco):.1f}%)\n"
                f"<b>💫 Parcial:</b> {fmt_price(preco + (preco - stop))} (+{pct(preco + (preco - stop), preco):.1f}%)\n\n"
                f"<b>⏰ {now_br()}</b>\n"
                "──────────────────────────────"
            )
            ok = await tg(session, msg)
            if ok:
                mark(symbol)

    except Exception as e:
        print(f"[ERRO SCAN] {symbol}: {e}")

# ---------------- MAIN LOOP ----------------
async def main_loop():
    async with aiohttp.ClientSession() as session:
        await tg(session, f"<b>BOT LONGA OURO INICIADO</b>\n<b>Cooldown:</b> 30m | <b>Vol 30m min:</b> US$ {VOL_30M_MIN_USDT/1_000_000:.0f}M\n<b>{now_br()}</b>")
        while True:
            start = time.time()
            symbols = await get_top_usdt_symbols(session)
            if not symbols:
                await asyncio.sleep(30)
                continue
            print(f"[{now_br()}] Escaneando {len(symbols)} pares...")
            await asyncio.gather(*[scan_symbol(session, s) for s in symbols])
            elapsed = time.time() - start
            print(f"[{now_br()}] Scan concluído em {elapsed:.1f}s. Próximo em 60s...")
            await asyncio.sleep(60)

# ---------------- EXECUÇÃO ----------------
def start_bot():
    while True:
        try:
            asyncio.run(main_loop())
        except Exception as e:
            print(f"[ERRO FATAL] {e}. Reiniciando em 5s...")
            time.sleep(5)

def run_flask_with_thread():
    print("[INFO] Inicializando BOT LONGA OURO...")
    t = threading.Thread(target=start_bot, daemon=True)
    t.start()
    print("[INFO] Thread principal do BOT iniciada.")
    app.run(host="0.0.0.0", port=int(os.getenv("PORT") or 10000))

if __name__ == "__main__":
    run_flask_with_thread()
```0
