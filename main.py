# main_long.py — V22.0L CLEANFLOW FIXED (15M Alinhamento Direcional)
import os, asyncio, aiohttp, time
from datetime import datetime, timedelta, timezone
from flask import Flask
import threading, statistics

app = Flask(__name__)
@app.route("/")
def home():
    return "V22.0L CLEANFLOW FIXED TENDÊNCIA LONGA ATIVO", 200

@app.route("/health")
def health():
    return "OK", 200

BINANCE = "https://api.binance.com"
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()

EXCLUDE = ["USDC", "USDP", "FDUSD", "TUSD", "USDE", "BUSD", "DAI", "EUR", "TRY", "BRL"]

def now_br():
    return (datetime.now(timezone.utc) - timedelta(hours=3)).strftime("%H:%M")

async def tg(s, msg):
    if not TELEGRAM_TOKEN:
        print(msg)
        return
    try:
        await s.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        print("Erro Telegram:", e)

def ema(data, p):
    if not data: return []
    a = 2 / (p + 1)
    e = data[0]
    out = [e]
    for x in data[1:]:
        e = a * x + (1 - a) * e
        out.append(e)
    return out

def rsi(prices, p=14):
    if len(prices) < p + 1: return 50
    d = [prices[i] - prices[i-1] for i in range(1, len(prices))]
    g = [max(x, 0) for x in d[-p:]]
    l = [abs(min(x, 0)) for x in d[-p:]]
    ag, al = sum(g) / p, sum(l) / p or 1e-12
    return 100 - 100 / (1 + ag / al)

async def klines(s, sym, tf, lim=250):
    url = f"{BINANCE}/api/v3/klines?symbol={sym}&interval={tf}&limit={lim}"
    async with s.get(url, timeout=10) as r:
        return await r.json() if r.status == 200 else []

async def ticker(s, sym):
    url = f"{BINANCE}/api/v3/ticker/24hr?symbol={sym}"
    async with s.get(url, timeout=10) as r:
        return await r.json() if r.status == 200 else None

cooldown_15m = {}
cooldown_4h = {}
cooldown_12h = {}
cooldown_1d = {}

def can_alert(tf, sym):
    cd = cooldown_15m if tf == "15m" else cooldown_4h if tf == "4h" else cooldown_12h if tf == "12h" else cooldown_1d
    cooldown_time = 1800 if tf == "15m" else 7200 if tf == "4h" else 14400 if tf == "12h" else 21600
    n = time.time()
    if n - cd.get(sym, 0) >= cooldown_time:
        cd[sym] = n
        return True
    return False

async def scan_tf(s, sym, tf):
    try:
        t = await ticker(s, sym)
        if not t: return
        p = float(t["lastPrice"])
        vol24 = float(t["quoteVolume"])
        if vol24 < 5_000_000: return
        if any(x in sym for x in EXCLUDE): return

        k = await klines(s, sym, tf, 250)
        if len(k) < 200: return
        close = [float(x[4]) for x in k]

        if tf == "15m":

            ma9_prev = sum(close[-10:-1]) / 9
            ma20_prev = sum(close[-21:-1]) / 20
            ma50_prev = sum(close[-51:-1]) / 50
            ma200_prev = sum(close[-201:-1]) / 200

            ma9_now = sum(close[-9:]) / 9
            ma20_now = sum(close[-20:]) / 20
            ma50_now = sum(close[-50:]) / 50
            ma200_now = sum(close[-200:]) / 200

            alta_antes = ma9_prev > ma20_prev > ma50_prev > ma200_prev
            alta_agora = ma9_now > ma20_now > ma50_now > ma200_now

            baixa_antes = ma9_prev < ma20_prev < ma50_prev < ma200_prev
            baixa_agora = ma9_now < ma20_now < ma50_now < ma200_now

            formou_alta = not alta_antes and alta_agora
            formou_baixa = not baixa_antes and baixa_agora

            if not (formou_alta or formou_baixa):
                return

            direcao = "🔼 SUBINDO" if formou_alta else "🔽 CAINDO"

        else:
            ema9_prev = ema(close[:-1], 9)
            ema20_prev = ema(close[:-1], 20)
            if len(ema9_prev) < 2 or len(ema20_prev) < 2:
                return

            alpha9 = 2 / (9 + 1)
            alpha20 = 2 / (20 + 1)
            ema9_atual = ema9_prev[-1] * (1 - alpha9) + close[-1] * alpha9
            ema20_atual = ema20_prev[-1] * (1 - alpha20) + close[-1] * alpha20

            cruzamento_agora = ema9_prev[-1] <= ema20_prev[-1] and ema9_atual > ema20_atual
            if not cruzamento_agora:
                return

        current_rsi = rsi(close)
        if current_rsi < 40 or current_rsi > 80:
            return

        prob = min(98, max(60, 70 + (current_rsi - 50) * 0.8))
        stop = min(float(x[3]) for x in k[-10:]) * 0.98
        alvo1 = p * 1.08
        alvo2 = p * 1.15
        nome = sym[:-4]

        if can_alert(tf, sym):

            if tf == "15m":
                titulo = f"<b>🌕 ALERTA 15M 🔶</b>\n\n<b>Alinhamento Recém-Formado — {direcao}</b>"
            elif tf == "4h":
                titulo = "<b>📊 TENDÊNCIA LONGA 4H 🔥🟣</b>\n\n<b>EMA9 CROSS CONFIRMADO — Continuidade de tendência</b>"
            elif tf == "12h":
                titulo = "<b>📊 TENDÊNCIA LONGA 12H 🌕🟠</b>\n\n<b>EMA9 CROSS CONFIRMADO — Continuidade de tendência</b>"
            else:
                titulo = "<b>📊 TENDÊNCIA LONGA 1D 🏆🌕</b>\n\n<b>EMA9 CROSS CONFIRMADO — Continuidade de tendência</b>"

            msg = (
                f"{titulo}\n\n"
                f"<b>{nome}</b>\n"
                f"<b>──────────────────────────</b>\n"
                f"<b>💰 Preço: {p:.6f}</b>\n"
                f"<b>📈 RSI: {current_rsi:.1f}</b>\n"
                f"<b>💵 Volume 24h: ${vol24:,.0f}</b>\n"
                f"<b>🌟 Prob: {prob:.0f}%</b>\n"
                f"<b>──────────────────────────</b>\n"
                f"<b>🛑 Stop: {stop:.6f}</b>\n"
                f"<b>🎯 +8%: {alvo1:.6f}</b>\n"
                f"<b>🏁 +15%: {alvo2:.6f}</b>\n"
                f"<b>──────────────────────────</b>\n"
                f"<b>⏱️ {now_br()} BR</b>"
            )

            await tg(s, msg)

    except Exception as e:
        print("Erro scan_tf:", e)

async def main_loop():
    async with aiohttp.ClientSession() as s:
        await tg(s, "<b>V22.0L CLEANFLOW FIXED — 15M Direcional</b>")
        while True:
            try:
                data = await (await s.get(f"{BINANCE}/api/v3/ticker/24hr")).json()
                symbols = [
                    d["symbol"] for d in data
                    if d["symbol"].endswith("USDT")
                    and float(d["quoteVolume"]) > 5_000_000
                    and not any(x in d["symbol"] for x in EXCLUDE)
                ]
                symbols = sorted(
                    symbols,
                    key=lambda x: next((float(t["quoteVolume"]) for t in data if t["symbol"] == x), 0),
                    reverse=True
                )[:100]
                tasks = []
                for sym in symbols:
                    tasks.append(scan_tf(s, sym, "15m"))
                    tasks.append(scan_tf(s, sym, "4h"))
                    tasks.append(scan_tf(s, sym, "12h"))
                    tasks.append(scan_tf(s, sym, "1d"))
                await asyncio.gather(*tasks)
            except Exception as e:
                print("Erro main_loop:", e)
            await asyncio.sleep(60)

threading.Thread(target=lambda: asyncio.run(main_loop()), daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT") or 10000)
    app.run(host="0.0.0.0", port=port)
