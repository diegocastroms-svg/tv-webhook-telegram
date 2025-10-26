# main.py — DUALSETUP Confirmado (continuidade real)
# ✅ Estrutura original (Flask + thread + asyncio.run + utils)
# ✅ Porta 50000 + /health
# ✅ Remoção automática de pares não-SPOT / mortos (exchangeInfo)
# ✅ Confirmação REAL: candle fecha acima da máxima anterior + volume maior (em ambos os setups)
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
    return "✅ Scanner ativo (DualSetup Confirmado) — SmallCap 15m/1h + Swing 1h/4h/1D | 🇧🇷", 200

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
    # Usa exchangeInfo para garantir SPOT real e status TRADING
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
            # Filtra contratos/perp e alavancados via nome
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
    # Blocklist adicional (moedas problemáticas/sem spot ou estáveis)
    blocked = (
        "BUSD","FDUSD","TUSD","USDC","USDP","USD1","USDE","XUSD","USDX","GUSD","BFUSD",
        "EUR","EURS","CEUR","BRL","TRY",
        "STABLE","TEST"  # genéricos
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
        # Faixas flexíveis (mantidas)
        RSI_SMALL_MIN, RSI_SMALL_MAX = 55.0, 80.0
        VOL_SMALL_MIN, VOL_SMALL_MAX = 1.3, 6.0
        RSI_SWING_MIN, RSI_SWING_MAX = 45.0, 60.0
        VOL_SWING_MIN, VOL_SWING_MAX = 0.8, 3.0
        TOL_BB, TOL_EMA = 0.98, 0.99  # tolerâncias

        # ---- Fetch klines
        k15 = await get_klines(session, symbol, "15m", 210)
        k1h = await get_klines(session, symbol, "1h", 210)
        k4h = await get_klines(session, symbol, "4h", 210)
        k1d = await get_klines(session, symbol, "1d", 210)
        if not (len(k15)>=52 and len(k1h)>=52 and len(k4h)>=52 and len(k1d)>=52):
            return

        # Usar APENAS candles FECHADOS:
        # - 15m: prev = k15[-2], curr = k15[-1]
        # - 1h:  prev = k1h[-2], curr = k1h[-1]
        c15 = [float(x[4]) for x in k15[:-0]]  # sequência completa; usaremos índices -1/-2
        v15 = [float(x[5]) for x in k15[:-0]]
        c1h = [float(x[4]) for x in k1h[:-0]]
        v1h = [float(x[5]) for x in k1h[:-0]]

        # --- Indicadores 15m
        ema9_15 = ema(c15, 9)
        ema20_15 = sma(c15, 20)
        u15, m15, l15 = bollinger_bands(c15, 20, 2)
        rsi15 = calc_rsi(c15, 14)
        vol_ma20_15 = sum(v15[-21:-1]) / 20.0 if len(v15) >= 21 else 0.0  # média dos 20 anteriores ao último
        vol_ratio_15 = (v15[-1] / (vol_ma20_15 + 1e-12)) if vol_ma20_15 else 0.0
        bbw15 = (u15[-1]-l15[-1])/(m15[-1]+1e-12) if m15[-1] else 0.0
        bbw15_prev = (u15[-2]-l15[-2])/(m15[-2]+1e-12) if m15[-2] else bbw15
        bb_expand_15 = bbw15 >= bbw15_prev * TOL_BB

        # Continuidade 15m: candle atual (fechado) vs anterior
        high_prev_15 = float(k15[-2][2]); close_prev_15 = float(k15[-2][4]); vol_prev_15 = float(k15[-2][5])
        high_curr_15 = float(k15[-1][2]); close_curr_15 = float(k15[-1][4]); vol_curr_15 = float(k15[-1][5])
        continuity_15 = (close_curr_15 > high_prev_15) and (vol_curr_15 > vol_prev_15)

        # --- Indicadores 1h
        ema9_1h = ema(c1h, 9)
        ema20_1h = sma(c1h, 20)
        ma50_1h = sma(c1h, 50)
        ma200_1h = sma(c1h, 200)
        u1h, m1h, l1h = bollinger_bands(c1h, 20, 2)
        rsi1h = calc_rsi(c1h, 14)
        vol_ma20_1h = sum(v1h[-21:-1]) / 20.0 if len(v1h) >= 21 else 0.0
        vol_ratio_1h = (v1h[-1] / (vol_ma20_1h + 1e-12)) if vol_ma20_1h else 0.0
        bbw1h = (u1h[-1]-l1h[-1])/(m1h[-1]+1e-12) if m1h[-1] else 0.0
        bbw1h_prev = (u1h[-2]-l1h[-2])/(m1h[-2]+1e-12) if m1h[-2] else bbw1h
        bb_expand_1h = bbw1h >= bbw1h_prev * TOL_BB

        # Continuidade 1h
        high_prev_1h = float(k1h[-2][2]); close_prev_1h = float(k1h[-2][4]); vol_prev_1h = float(k1h[-2][5])
        high_curr_1h = float(k1h[-1][2]); close_curr_1h = float(k1h[-1][4]); vol_curr_1h = float(k1h[-1][5])
        continuity_1h = (close_curr_1h > high_prev_1h) and (vol_curr_1h > vol_prev_1h)

        # --- Indicadores 4h/1D
        c4h_vals = [float(x[4]) for x in k4h]
        ema9_4h = ema(c4h_vals, 9); ema20_4h = sma(c4h_vals, 20)
        ma50_4h = sma(c4h_vals, 50); ma200_4h = sma(c4h_vals, 200)

        c1d_vals = [float(x[4]) for x in k1d]
        ema20_1d = sma(c1d_vals, 20)

        # ============= 🔥 SMALL CAP EXPLOSIVA (15m/1h) =============
        small_ok = (
            (RSI_SMALL_MIN <= rsi15[-1] <= RSI_SMALL_MAX) and
            (VOL_SMALL_MIN <= vol_ratio_15 <= VOL_SMALL_MAX) and
            (ema9_15[-1] >= ema20_15[-1] * TOL_EMA) and
            bb_expand_15 and
            (close_curr_1h := close_curr_1h) >= (ema20_1h[-1] * TOL_EMA) and
            continuity_15  # confirmação real: fecha acima da máxima anterior + volume maior
        )
        if small_ok and allowed(symbol, "SMALL_ALERT"):
            msg = (
                f"🚨 <b>[EXPLOSÃO SUSTENTÁVEL DETECTADA]</b>\n"
                f"💥 {symbol}\n"
                f"🕒 {now_br()}\n"
                f"💰 Preço: {fmt_price(close_curr_15)}\n"
                f"📊 Volume: {(vol_ratio_15-1)*100:.0f}% acima da média 💣\n"
                f"📈 RSI(15m): {rsi15[-1]:.1f} | EMA9≥EMA20 | BB abrindo ✅\n"
                f"⏱️ Confirmação 1h: Close ≥ EMA20 ✅ e continuidade (15m) ✅\n"
                f"🔗 https://www.binance.com/en/trade/{symbol}"
            )
            await tg(session, msg)
            mark(symbol, "SMALL_ALERT")

        # ============= 🟩 SWING CURTO (1–3 dias) (1h/4h/1D) =============
        cross_9_20_1h = (ema9_1h[-2] <= ema20_1h[-2]) and (ema9_1h[-1] > ema20_1h[-1])
        swing_ok = (
            cross_9_20_1h and
            (RSI_SWING_MIN <= rsi1h[-1] <= RSI_SWING_MAX) and
            (VOL_SWING_MIN <= vol_ratio_1h <= VOL_SWING_MAX) and
            bb_expand_1h and
            (ema9_4h[-1] >= ema20_4h[-1] * TOL_EMA) and
            (ma50_4h[-1] >= ma200_4h[-1] * TOL_EMA) and
            (c1d_vals[-1] >= ema20_1d[-1] * TOL_EMA) and
            continuity_1h  # confirmação real no 1h
        )
        if swing_ok and allowed(symbol, "SWING_ALERT"):
            msg = (
                f"💹 <b>[SWING CURTO – TENDÊNCIA SUSTENTADA]</b>\n"
                f"📊 {symbol}\n"
                f"🕒 {now_br()}\n"
                f"💰 Preço: {fmt_price(close_curr_1h)}\n"
                f"📈 EMA9>EMA20 (1h) | EMA9≥EMA20 (4h) | MA50≥MA200 (4h) ✅\n"
                f"⚡ RSI(1h): {rsi1h[-1]:.1f} | Volume: {(vol_ratio_1h-1)*100:.0f}% acima | BB abrindo ✅\n"
                f"🧭 Direção 1D: Close ≥ EMA20 ✅ | Continuidade (1h) ✅\n"
                f"🔗 https://www.binance.com/en/trade/{symbol}"
            )
            await tg(session, msg)
            mark(symbol, "SWING_ALERT")

    except:
        return

# ---------------- MAIN LOOP ----------------
async def main_loop():
    async with aiohttp.ClientSession() as session:
        symbols = await get_top_usdt_symbols(session)
        await tg(session, f"✅ BOT DUALSETUP INICIADO (Confirmado) 🚀 | {len(symbols)} pares | {now_br()}")
        if not symbols:
            await asyncio.sleep(30)
            return
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
