import os, asyncio, aiohttp, time
from datetime import datetime, timedelta, timezone
from flask import Flask
import threading

app = Flask(__name__)

@app.route("/")
def home():
    return "SENTINELA DIÁRIO - 150 ATIVOS - OK", 200

BINANCE = "https://fapi.binance.com"
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()
EXCLUDE = ["USDC", "USDP", "FDUSD", "TUSD", "USDE", "BUSD", "DAI", "EUR", "TRY", "BRL"]

def now_br():
    return (datetime.now(timezone.utc) - timedelta(hours=3)).strftime("%H:%M")

async def tg(s, msg):
    if not TELEGRAM_TOKEN or not CHAT_ID: return
    try:
        async with s.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=5
        ) as r:
            pass
    except Exception as e:
        print(f"Erro Telegram: {e}")

def ema_calc(data, p):
    if len(data) < p: return [data[-1]]
    alpha = 2 / (p + 1)
    # Inicializa com SMA para estabilizar
    ema = [sum(data[:p]) / p]
    for i in range(p, len(data)):
        ema.append((data[i] - ema[-1]) * alpha + ema[-1])
    return ema

def calc_bb(data):
    if len(data) < 20: return 0, 0
    slice_data = data[-20:]
    sma = sum(slice_data) / 20
    std = (sum((x - sma) ** 2 for x in slice_data) / 20) ** 0.5
    return sma + (2 * std), sma - (2 * std)

cooldowns = {}

async def scan_tf(s, sym):
    try:
        # Aumentado para 250 para estabilizar o cálculo da EMA 200
        url = f"{BINANCE}/fapi/v1/klines?symbol={sym}&interval=1d&limit=250"
        async with s.get(url, timeout=10) as r:
            if r.status != 200: return
            k = await r.json()

        if len(k) < 201: return

        close = [float(x[4]) for x in k]
        p_now = close[-1]
        p_prev = close[-2]
        
        all_ema = ema_calc(close, 200)
        ema200 = all_ema[-1]
        
        bb_up_now, bb_low_now = calc_bb(close)
        bb_up_prev, bb_low_prev = calc_bb(close[:-1])

        dist = abs(p_now - ema200) / ema200
        perto = dist <= 0.03
        cruzou = (p_prev < ema200 <= p_now) or (p_prev > ema200 >= p_now)

        if not (perto or cruzou): return

        # COOLDOWN DE 1 HORA
        key = f"1d_{sym}"
        if time.time() - cooldowns.get(key, 0) < 3600: return

        nome = sym.replace("USDT", "")

        # LOGICA LONG
        if p_now >= bb_up_now and bb_up_now > bb_up_prev:
            cooldowns[key] = time.time()
            msg = (f"🟩⏫ <b>ALERTA DIÁRIO LONG</b>\n\n"
                   f"<b>💎 {nome} 💎</b>\n"
                   f"💰 Preço: {p_now:.6f}\n"
                   f"📏 Dist. EMA 200: {dist:.2%}\n"
                   f"⏱️ {now_br()} BR")
            await tg(s, msg)

        # LOGICA SHORT
        elif p_now <= bb_low_now and bb_low_now < bb_low_prev:
            cooldowns[key] = time.time()
            msg = (f"🟥⏬ <b>ALERTA DIÁRIO SHORT</b>\n\n"
                   f"<b>💎 {nome} 💎</b>\n"
                   f"💰 Preço: {p_now:.6f}\n"
                   f"📏 Dist. EMA 200: {dist:.2%}\n"
                   f"⏱️ {now_br()} BR")
            await tg(s, msg)

    except Exception as e:
        pass # Silencia erros individuais de moedas

async def main_loop():
    # Connector para evitar overhead de conexões abertas
    connector = aiohttp.TCPConnector(limit=5)
    async with aiohttp.ClientSession(connector=connector) as s:
        await tg(s, "<b>🚀 SENTINELA DIÁRIO ATIVO</b>\n<i>Monitorando 150 ativos</i>")
        while True:
            try:
                async with s.get(f"{BINANCE}/fapi/v1/ticker/24hr") as r:
                    if r.status == 200:
                        data = await r.json()
                        
                        # Filtro de ativos USDT excluindo moedas lixo/estáveis
                        symbols_data = [
                            d for d in data
                            if d["symbol"].endswith("USDT")
                            and not any(x in d["symbol"] for x in EXCLUDE)
                        ]
                        
                        # Ordena e pega as 150 moedas com mais volume
                        top_symbols = sorted(symbols_data, 
                                            key=lambda x: float(x["quoteVolume"]), reverse=True)[:150]

                        for item in top_symbols:
                            await scan_tf(s, item["symbol"])
                            await asyncio.sleep(0.1) # Delay anti-ban

            except Exception as e:
                print(f"Erro Loop: {e}")

            # Espera 1 hora (ou 30 min se quiser mais sensibilidade)
            await asyncio.sleep(3600)

if __name__ == "__main__":
    # Inicia o scanner
    t = threading.Thread(target=lambda: asyncio.run(main_loop()), daemon=True)
    t.start()
    
    # Inicia o servidor para o Render/Heroku
    port = int(os.environ.get("PORT") or 10000)
    app.run(host="0.0.0.0", port=port)
