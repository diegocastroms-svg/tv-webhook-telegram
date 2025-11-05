# main_long.py — V21.2L SINCRONIZADO (4H, 12H, 1D)
import os, asyncio, aiohttp, time
from datetime import datetime, timedelta, timezone
from flask import Flask
import threading

app = Flask(__name__)
@app.route("/")
def home():
    return "V21.2L SINCRONIZADO ATIVO", 200

@app.route("/health")
def health():
    return "OK", 200

BINANCE = "https://api.binance.com"
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()

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
            timeout=10
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

def sma_series(data, p):
    if len(data) < p: return []
    s = sum(data[:p])
    out = [s / p]
    for i in range(p, len(data)):
        s += data[i] - data[i - p]
        out.append(s / p)
    return out

def rsi(prices, p=14):
    if len(prices) < p + 1: return 50
    d = [prices[i] - prices[i-1] for i in range(1, len(prices))]
    g = [max(x, 0) for x in d[-p:]]
    l = [abs(min(x, 0)) for x in d[-p:]]
    ag, al = sum(g) / p, sum(l) / p or 1e-12
    return 100 - 100 / (1 + ag / al)

async def klines(s, sym, tf, lim=100):
    url = f"{BINANCE}/api/v3/klines?symbol={sym}&interval={tf}&limit={lim}"
    async with s.get(url, timeout=10) as r:
        return await r.json() if r.status == 200 else []

async def ticker(s, sym):
    url = f"{BINANCE}/api/v3/ticker/24hr?symbol={sym}"
    async with s.get(url, timeout=10) as r:
        return await r.json() if r.status == 200 else None

# cooldowns individuais (guarda timestamp do último alerta por tf/símbolo)
cooldowns = {tf: {} for tf in ["4h", "12h", "1d"]}

def can_alert(tf, sym):
    cd = cooldowns[tf]
    cooldown_time = {"4h": 14400, "12h": 43200, "1d": 86400}[tf]
    n = time.time()
    if n - cd.get(sym, 0) >= cooldown_time:
        cd[sym] = n
        return True
    return False

# controle de sincronismo: processa apenas 1x por vela fechada
# guarda o open_time (ms) da ÚLTIMA vela FECHADA já processada por tf/símbolo
last_processed = {tf: {} for tf in ["4h", "12h", "1d"]}

async def scan_tf(s, sym, tf):
    try:
        t = await ticker(s, sym)
        if not t: return
        p = float(t["lastPrice"])
        vol24 = float(t["quoteVolume"])
        if vol24 < 10_000_000: return  # VOLUME 10M

        k = await klines(s, sym, tf, 100)
        if len(k) < 50: return

        # k[-1] = vela em formação; última FECHADA = k[-2]
        # open_time em ms:
        last_closed_open_ms = int(k[-2][0])
        # se já processamos essa vela fechada, sair
        if last_processed[tf].get(sym) == last_closed_open_ms:
            return

        # série de closes SOMENTE com velas FECHADAS (exclui k[-1])
        close = [float(x[4]) for x in k[:-1]]
        if len(close) < 22:  # margem para MA20, cruzamento e RSI
            return

        # EMA9 e MA20 em velas FECHADAS
        ema9 = ema(close, 9)
        ma20 = sma_series(close, 20)
        if len(ema9) < 2 or len(ma20) < 2:
            return

        # cruzamento confirmado ENTRE velas fechadas
        cruzamento_confirmado = (ema9[-2] <= ma20[-2]) and (ema9[-1] > ma20[-1])
        if not cruzamento_confirmado:
            # atualiza last_processed mesmo sem alerta para não reprocessar sem necessidade?
            # não: só marcamos quando realmente analisamos o fechamento atual.
            # ainda assim, marcamos para evitar loop contínuo no mesmo fechamento.
            last_processed[tf][sym] = last_closed_open_ms
            return

        # filtros adicionais
        current_rsi = rsi(close)
        if current_rsi < 40 or current_rsi > 80:
            last_processed[tf][sym] = last_closed_open_ms
            return

        # preço atual precisa estar acima da EMA9 mais recente (de vela FECHADA)
        if p < ema9[-1] * 0.999:  # folga 0,1%
            last_processed[tf][sym] = last_closed_open_ms
            return

        # dispara 1x por vela fechada + respeita cooldown de janela
        if can_alert(tf, sym):
            stop = min(float(x[3]) for x in k[-11:-1]) * 0.98  # últimos 10 lows de velas FECHADAS
            alvo1 = p * 1.08
            alvo2 = p * 1.15
            prob = {"4h": "88%", "12h": "90%", "1d": "93%"}[tf]
            emoji = {"4h": "🔥", "12h": "🌕", "1d": "🏆"}[tf]
            color = {"4h": "🟣", "12h": "🟠", "1d": "🟡"}[tf]
            msg = (
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"📊 <b>TENDÊNCIA LONGA {tf.upper()}</b> {emoji} {color}\n"
                f"<code>{sym}</code>\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"💰 Preço: <b>{p:.6f}</b>\n"
                f"📈 RSI: <b>{current_rsi:.1f}</b>\n"
                f"💵 Volume: <b>${vol24:,.0f}</b>\n"
                f"🌟 Probabilidade: <b>{prob}</b>\n\n"
                f"🛑 Stop: <b>{stop:.6f}</b>\n"
                f"🎯 +8%: <b>{alvo1:.6f}</b>\n"
                f"🏁 +15%: <b>{alvo2:.6f}</b>\n"
                f"🕒 {now_br()} BR\n"
                f"━━━━━━━━━━━━━━━━━━━"
            )
            await tg(s, msg)

        # marca a vela fechada como processada (com ou sem alerta, após filtros)
        last_processed[tf][sym] = last_closed_open_ms

    except Exception as e:
        print("Erro scan_tf:", e)

async def main_loop():
    async with aiohttp.ClientSession() as s:
        await tg(s, "<b>V21.2L SINCRONIZADO ATIVO</b>\n4H, 12H e 1D Monitorando 🔥")
        while True:
            try:
                data = await (await s.get(f"{BINANCE}/api/v3/ticker/24hr")).json()
                symbols = [
                    d["symbol"] for d in data
                    if d["symbol"].endswith("USDT")
                    and float(d["quoteVolume"]) > 10_000_000
                    and (lambda base: not (
                        base.endswith("USD")
                        or base in {
                            "BUSD","FDUSD","USDE","USDC","TUSD","CUSD",
                            "EUR","GBP","TRY","AUD","BRL","RUB","CAD","CHF","JPY",
                            "BF","BFC","BFG","BFD","BETA","AEUR","AUSD","CEUR","XAUT"
                        }
                    ))(d["symbol"][:-4])
                    and not any(x in d["symbol"] for x in ["UP", "DOWN"])
                ]
                symbols = sorted(
                    symbols,
                    key=lambda x: next((float(t["quoteVolume"]) for t in data if t["symbol"] == x), 0),
                    reverse=True
                )[:100]

                tasks = []
                for sym in symbols:
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
