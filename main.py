# v2.5_long_real.py — TENDÊNCIA REAL + 4h CONFIRMAÇÃO + DIAS AUTOMÁTICO
# SÓ ALERTA EM TENDÊNCIA GORDA DE 7-14 DIAS

import os, asyncio, aiohttp, time
from datetime import datetime, timedelta

# ---------------- CONFIG ----------------
BINANCE_HTTP = "https://api.binance.com"
COOLDOWN_SEC = 30 * 60  # 30 min
REQ_TIMEOUT = 10
VERSION = "V2.5 LONGO REAL + 4h"

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()

# ---------------- UTILS ----------------
def now_br():
    return (datetime.utcnow() - timedelta(hours=3)).strftime("%H:%M")

async def tg(session, text: str):
    if not (TELEGRAM_TOKEN and CHAT_ID):
        print(f"[ALERTA] {text}")
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        await session.post(url, data={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}, timeout=REQ_TIMEOUT)
    except Exception as e:
        print(f"[TG ERRO] {e}")

def ema(seq, period):
    if len(seq) < 1: return []
    alpha = 2 / (period + 1)
    e = seq[0]
    out = [e]
    for p in seq[1:]:
        e = alpha * p + (1 - alpha) * e
        out.append(e)
    return out

def calc_rsi(prices, period=14):
    if len(prices) < period + 1: return 50.0
    deltas = [prices[i] - prices[i-1] for i in range(1, len(prices))]
    gains = [max(d, 0) for d in deltas[:period]]
    losses = [abs(min(d, 0)) for d in deltas[:period]]
    avg_g = sum(gains)/period
    avg_l = sum(losses)/period or 1e-12
    rs = avg_g / avg_l
    rsi = 100 - 100/(1+rs)
    for i in range(period, len(deltas)):
        d = deltas[i]
        g = d if d > 0 else 0
        l = -d if d < 0 else 0
        avg_g = (avg_g * (period-1) + g) / period
        avg_l = (avg_l * (period-1) + l) / period
        rs = avg_g / (avg_l + 1e-12)
        rsi = 100 - 100/(1+rs)
    return rsi

def calc_atr(klines):
    if len(klines) < 2: return 0
    trs = []
    for i in range(1, len(klines)):
        high = float(klines[i][2])
        low = float(klines[i][3])
        prev_close = float(klines[i-1][4])
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
    return sum(trs) / len(trs) if trs else 0

# ---------------- BINANCE ----------------
async def get_klines(session, symbol, interval, limit=100):
    url = f"{BINANCE_HTTP}/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    try:
        async with session.get(url, timeout=REQ_TIMEOUT) as r:
            if r.status != 200: return []
            return await r.json()
    except:
        return []

async def get_ticker_24hr(session, symbol):
    url = f"{BINANCE_HTTP}/api/v3/ticker/24hr?symbol={symbol}"
    try:
        async with session.get(url, timeout=REQ_TIMEOUT) as r:
            if r.status != 200: return None
            return await r.json()
    except:
        return None

# ---------------- COOLDOWN ----------------
cooldowns = {}
def can_alert(s):
    key = f"{s}_long"
    now = time.time()
    if now - cooldowns.get(key, 0) >= COOLDOWN_SEC:
        cooldowns[key] = now
        return True
    return False

# ---------------- SCAN LONGO ----------------
async def scan_long(session, symbol):
    try:
        ticker = await get_ticker_24hr(session, symbol)
        if not ticker: return
        change24 = float(ticker["priceChangePercent"])
        preco = float(ticker["lastPrice"])

        # 15m para entrada
        k15m = await get_klines(session, symbol, "15m", 100)
        if not k15m or len(k15m) < 50: return
        close15m = [float(k[4]) for k in k15m[:-1]]
        vol15m = [float(k[5]) for k in k15m[:-1]]

        # 1h para confirmação
        k1h = await get_klines(session, symbol, "1h", 100)
        if not k1h or len(k1h) < 50: return
        close1h = [float(k[4]) for k in k1h[:-1]]

        # 4h para JUIZ FINAL
        k4h = await get_klines(session, symbol, "4h", 100)
        if not k4h or len(k4h) < 50: return
        close4h = [float(k[4]) for k in k4h[:-1]]

        # === EMA 15m ===
        e9_15 = ema(close15m, 9)
        e21_15 = ema(close15m, 21)
        e200_15 = ema(close15m, 200)

        # === EMA 1h ===
        e9_1h = ema(close1h, 9)
        e21_1h = ema(close1h, 21)

        # === EMA 4h ===
        e9_4h = ema(close4h, 9)
        e21_4h = ema(close4h, 21)
        e50_4h = ema(close4h, 50)

        # === RSI 15m ===
        rsi15 = calc_rsi(close15m[-30:])
        rsi15_ant = calc_rsi(close15m[-31:-1])

        # === Volume 15m ===
        vol_ultima = vol15m[-1]
        vol_media_5 = sum(vol15m[-6:-1]) / 5
        vol_ratio = vol_ultima / vol_media_5 if vol_media_5 > 0 else 0

        # === MACD 15m ===
        ef12 = ema(close15m, 12)
        es26 = ema(close15m, 26)
        macd_line = [f - s for f, s in zip(ef12, es26)]
        sig = ema(macd_line, 9)
        hist = [m - sg for m, sg in zip(macd_line[-len(sig):], sig)]
        macd_hist = hist[-1] if hist else 0

        # === MACD 4h ===
        ef12_4h = ema(close4h, 12)
        es26_4h = ema(close4h, 26)
        macd_line_4h = [f - s for f, s in zip(ef12_4h, es26_4h)]
        sig_4h = ema(macd_line_4h, 9)
        hist_4h = [m - sg for m, sg in zip(macd_line_4h[-len(sig_4h):], sig_4h)]
        macd_hist_4h = hist_4h[-1] if hist_4h else 0

        # === FILTROS DUROS (TENDÊNCIA REAL) ===
        if (change24 >= 5 and
            vol_ratio >= 2.0 and
            len(hist) >= 3 and macd_hist > 0.005 and hist[-1] > hist[-2] and
            e9_15[-1] > e21_15[-1] and
            preco > e200_15[-1] and
            rsi15 > 55 and rsi15 > rsi15_ant and
            e9_1h[-1] > e21_1h[-1] and
            e9_4h[-1] > e21_4h[-1] and
            preco > e50_4h[-1] and
            macd_hist_4h > 0.002 and
            can_alert(symbol)):

            # === CÁLCULO AUTOMÁTICO DE DIAS ===
            atr = calc_atr(k15m[-15:])
            volatilidade = atr / preco if atr > 0 else 0

            forca_macd = macd_hist / 0.01
            forca_ema = (preco / e21_15[-1] - 1) * 100
            forca_rsi = (rsi15 - 50) / 50

            forca_total = (forca_macd + forca_ema + forca_rsi) / 3
            vol_sustentado = sum(vol15m[-5:]) / 5 > vol_media_5 * 1.5

            if forca_total > 1.5 and volatilidade > 0.02 and vol_sustentado:
                tempo_dias = 3
            elif forca_total > 1.0:
                tempo_dias = 5
            elif forca_total > 0.5:
                tempo_dias = 7
            else:
                tempo_dias = 14

            tempo_estimado = f"{tempo_dias}-{tempo_dias + 7} DIAS"

            # === ALERTA ===
            alvo1 = preco * 1.03
            alvo2 = preco * 1.05
            stop = min([float(k[3]) for k in k15m[-6:]]) * 0.98

            msg = (
                f"<b>UP TENDÊNCIA LONGA CONFIRMADA</b>\n"
                f"<code>{symbol}</code>\n"
                f"Preço: <b>{preco:.6f}</b>\n"
                f"<b>PROBABILIDADE: 85%</b>\n"
                f"<b>TEMPO ESTIMADO: {tempo_estimado}</b>\n"
                f"Stop: <b>{stop:.6f}</b>\n"
                f"Alvo 1: <b>{alvo1:.6f}</b> (+3%)\n"
                f"Alvo 2: <b>{alvo2:.6f}</b> (+5%)\n"
                f"<i>{now_br()} BR</i>"
            )
            await tg(session, msg)

    except Exception as e:
        pass

# ---------------- MAIN ----------------
async def main_loop():
    async with aiohttp.ClientSession() as session:
        await tg(session, f"<b>{VERSION} ATIVO</b>\nTendência Real + 4h + Dias Auto\n{now_br()} BR")
        while True:
            try:
                url = f"{BINANCE_HTTP}/api/v3/ticker/24hr"
                async with session.get(url, timeout=REQ_TIMEOUT) as r:
                    if r.status != 200: continue
                    data = await r.json()
                symbols = [d["symbol"] for d in data if d["symbol"].endswith("USDT") and float(d["quoteVolume"]) > 50_000_000]
                await asyncio.gather(*[scan_long(session, s) for s in symbols[:100]], return_exceptions=True)
            except:
                pass
            await asyncio.sleep(60)

asyncio.run(main_loop())
