import os, asyncio, aiohttp, time
from datetime import datetime, timedelta, timezone
from flask import Flask
import threading

app = Flask(__name__)

# ESTRUTURA DE ROTAS IGUAL AO ORIGINAL
@app.route("/")
def home():
    return "SENTINELA DIÁRIO - ATIVO",200

@app.route("/health")
def health():
    return "OK",200


BINANCE="https://fapi.binance.com"
TELEGRAM_TOKEN=os.getenv("TELEGRAM_TOKEN","").strip()
CHAT_ID=os.getenv("CHAT_ID","").strip()

EXCLUDE=["USDC","USDP","FDUSD","TUSD","USDE","BUSD","DAI","EUR","TRY","BRL"]

# FUNÇÕES TÉCNICAS ADICIONADAS PARA O NOVO SETUP
def ema_calc(data, p):
    if len(data) < p: return [data[-1]]
    alpha = 2 / (p + 1)
    ema = [sum(data[:p]) / p]
    for i in range(p, len(data)):
        ema.append((data[i] - ema[-1]) * alpha + ema[-1])
    return ema

def calc_bb(data):
    if len(data) < 20: return 0, 0
    w = data[-20:]
    sma = sum(w) / 20
    std = (sum((x - sma) ** 2 for x in w) / 20) ** 0.5
    return sma + (2 * std), sma - (2 * std)

def now_br():
    return (datetime.now(timezone.utc)-timedelta(hours=3)).strftime("%H:%M")

async def tg(s,msg):
    if not TELEGRAM_TOKEN:
        print(msg)
        return
    try:
        await s.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id":CHAT_ID,"text":msg,"parse_mode":"HTML"},
            timeout=10
        )
    except Exception as e:
        print("Erro Telegram:",e)

# AJUSTADO LIMITE PARA 250 PARA PRECISÃO DA EMA 200
async def klines(s,sym,tf,lim=250):
    url=f"{BINANCE}/fapi/v1/klines?symbol={sym}&interval={tf}&limit={lim}"
    async with s.get(url,timeout=10) as r:
        return await r.json() if r.status==200 else []

cooldowns={}

def can_alert(sym):
    key=f"1d_{sym}"
    cd=cooldowns.get(key,0)
    n=time.time()
    # COOLDOWN DE 1 HORA (3600s)
    if n-cd>=3600:
        cooldowns[key]=n
        return True
    return False

# FUNÇÃO DE SCAN ADAPTADA PARA O NOVO SETUP DIÁRIO
async def scan_tf(s,sym):
    try:
        # Pega klines de 1 Dia
        k = await klines(s,sym,"1d",250)
        if len(k)<210: return

        close=[float(x[4]) for x in k]
        p_now=close[-1]
        p_prev=close[-2]

        # Cálculos do novo setup
        all_ema = ema_calc(close, 200)
        ema200 = all_ema[-1]
        
        bb_up_now, bb_low_now = calc_bb(close)
        bb_up_prev, bb_low_prev = calc_bb(close[:-1])

        # Lógica de 3% de distância ou cruzamento
        dist = abs(p_now - ema200) / ema200
        perto = dist <= 0.03
        cruzou = (p_prev < ema200 <= p_now) or (p_prev > ema200 >= p_now)

        if not (perto or cruzou): return

        nome=sym[:-4]

        # GATILHO LONG (Preço na Banda Superior + Banda abrindo pra cima)
        if p_now >= bb_up_now and bb_up_now > bb_up_prev:
            if can_alert(sym):
                msg=(f"🟩⏫ <b>ALERTA DIÁRIO LONG</b>\n\n"
                     f"<b>💎 {nome} 💎</b>\n"
                     f"💰 Preço: {p_now:.6f}\n"
                     f"📏 Dist. EMA 200: {dist:.2%}\n"
                     f"⏱️ {now_br()} BR")
                await tg(s,msg)

        # GATILHO SHORT (Preço na Banda Inferior + Banda abrindo pra baixo)
        elif p_now <= bb_low_now and bb_low_now < bb_low_prev:
            if can_alert(sym):
                msg=(f"🟥⏬ <b>ALERTA DIÁRIO SHORT</b>\n\n"
                     f"<b>💎 {nome} 💎</b>\n"
                     f"💰 Preço: {p_now:.6f}\n"
                     f"📏 Dist. EMA 200: {dist:.2%}\n"
                     f"⏱️ {now_br()} BR")
                await tg(s,msg)

    except Exception as e:
        print(f"Erro scan_tf {sym}:",e)

async def main_loop():
    async with aiohttp.ClientSession() as s:
        await tg(s,"<b>SENTINELA DIÁRIO ATIVO</b>")
        while True:
            try:
                # Busca tickers para filtrar volume
                resp = await s.get(f"{BINANCE}/fapi/v1/ticker/24hr")
                data = await resp.json()

                symbols=[
                    d["symbol"] for d in data
                    if d["symbol"].endswith("USDT")
                    and not any(x in d["symbol"] for x in EXCLUDE)
                ]

                # SELECIONA OS 150 ATIVOS COM MAIOR VOLUME (QUOTE VOLUME)
                top_symbols = sorted([d for d in data if d["symbol"] in symbols], 
                                    key=lambda x: float(x["quoteVolume"]), reverse=True)[:150]

                for item in top_symbols:
                    await scan_tf(s, item["symbol"])
                    await asyncio.sleep(0.1) # Delay anti-ban

            except Exception as e:
                print("Erro main_loop:",e)

            await asyncio.sleep(3600) # VERIFICA A CADA 1 HORA

# ESTRUTURA DE INICIALIZAÇÃO IGUAL AO ORIGINAL
threading.Thread(target=lambda: asyncio.run(main_loop()),daemon=True).start()

if __name__=="__main__":
    port=int(os.environ.get("PORT") or 10000)
    app.run(host="0.0.0.0",port=port)
