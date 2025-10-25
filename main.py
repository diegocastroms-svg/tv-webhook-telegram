import os, asyncio, time, math
from urllib.parse import urlencode
from collections import defaultdict, deque
from datetime import datetime, timezone, timedelta
import aiohttp
from flask import Flask
import threading
import logging

# ---------------- CONFIG ----------------
BINANCE_HTTP = "https://api.binance.com"
INTERVAL_5M, INTERVAL_15M = "5m", "15m"

# Variáveis de ambiente
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

# ---------------- LOGS ----------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")

app = Flask(__name__)

# ---------------- HEALTH ----------------
@app.route("/health")
def health():
    return "OK", 200

# ---------------- FUNÇÕES ----------------
async def send_telegram_message(message: str):
    """Envia mensagem formatada para o Telegram"""
    if not TELEGRAM_TOKEN or not CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"}
        async with aiohttp.ClientSession() as session:
            async with session.post(url, data=data) as resp:
                if resp.status != 200:
                    logging.warning(f"Falha ao enviar mensagem Telegram: {resp.status}")
    except Exception as e:
        logging.error(f"Erro ao enviar mensagem Telegram: {e}")

async def fetch_data(session, symbol, interval):
    try:
        params = {"symbol": symbol, "interval": interval, "limit": 200}
        async with session.get(f"{BINANCE_HTTP}/api/v3/klines", params=params) as resp:
            return await resp.json()
    except Exception:
        return []

async def analyze_symbol(session, symbol):
    """Analisa um símbolo e envia alerta se cruzamento indicar alta"""
    try:
        for interval in [INTERVAL_5M, INTERVAL_15M]:
            data = await fetch_data(session, symbol, interval)
            if not data or len(data) < 200:
                continue

            closes = [float(x[4]) for x in data]
            ema9 = sum(closes[-9:]) / 9
            ma20 = sum(closes[-20:]) / 20
            ma50 = sum(closes[-50:]) / 50
            ma200 = sum(closes[-200:]) / 200
            price = closes[-1]

            if ema9 > ma200 and price > ma20:
                msg = f"🚀 <b>{symbol}</b> possível alta ({interval})\n💰 Preço: {price:.4f}"
                await send_telegram_message(msg)
            
            # Anti-flood Binance (respeita limites de request)
            await asyncio.sleep(0.5)

    except Exception as e:
        logging.error(f"Erro ao analisar {symbol}: {e}")

async def monitor_market():
    """Loop principal do bot"""
    logging.info("BOT DUALSETUP INICIADO ✅")
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{BINANCE_HTTP}/api/v3/exchangeInfo") as resp:
                    data = await resp.json()
                    symbols = [s["symbol"] for s in data["symbols"] if s["symbol"].endswith("USDT")]
                tasks = [asyncio.create_task(analyze_symbol(session, s)) for s in symbols]
                await asyncio.gather(*tasks)
            await asyncio.sleep(300)  # 5 minutos
        except Exception as e:
            logging.error(f"Erro no loop principal: {e}")
            await asyncio.sleep(60)

def start_bot():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(monitor_market())

# ---------------- MAIN ----------------
if __name__ == "__main__":
    def start_after_ready():
        time.sleep(3)
        logging.info("BOT DUALSETUP INICIADO ✅")
        start_bot()

    threading.Thread(target=start_after_ready, daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 50000)), use_reloader=False)
