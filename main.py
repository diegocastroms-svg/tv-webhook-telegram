import os, asyncio, aiohttp, time
from datetime import datetime, timedelta, timezone
from flask import Flask
import threading

app = Flask(__name__)

@app.route("/")
def home():
    return "SCANNER MÉDIAS 9 20 50",200

@app.route("/health")
def health():
    return "OK",200


BINANCE="https://fapi.binance.com"
TELEGRAM_TOKEN=os.getenv("TELEGRAM_TOKEN","").strip()
CHAT_ID=os.getenv("CHAT_ID","").strip()

EXCLUDE=["USDC","USDP","FDUSD","TUSD","USDE","BUSD","DAI","EUR","TRY","BRL"]

TF_COLOR={
"15m":"🟦",
"4h":"🟩",
"1d":"🟨",
"3d":"🟧",
"1w":"🟥",
"1M":"🟪"
}

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


def ma(data,p):

    if not data: return []

    out=[]

    for i in range(len(data)):
        if i+1<p:
            out.append(data[i])
        else:
            w=data[i+1-p:i+1]
            out.append(sum(w)/p)

    return out


async def klines(s,sym,tf,lim=100):

    url=f"{BINANCE}/fapi/v1/klines?symbol={sym}&interval={tf}&limit={lim}"

    async with s.get(url,timeout=10) as r:
        return await r.json() if r.status==200 else []


async def ticker(s,sym):

    url=f"{BINANCE}/fapi/v1/ticker/24hr?symbol={sym}"

    async with s.get(url,timeout=10) as r:
        return await r.json() if r.status==200 else None



cooldowns={}

def can_alert(tf,sym):

    if tf=="15m":
        return True

    key=f"{tf}_{sym}"
    cd=cooldowns.get(key,0)

    if tf=="4h": cooldown_time=7200
    elif tf=="1d": cooldown_time=21600
    else: cooldown_time=86400

    n=time.time()

    if n-cd>=cooldown_time:
        cooldowns[key]=n
        return True

    return False



async def scan_tf(s,sym,tf):

    try:

        t=await ticker(s,sym)
        if not t: return

        p=float(t["lastPrice"])
        vol24=float(t["quoteVolume"])

        if vol24<1_000_000: return
        if any(x in sym for x in EXCLUDE): return

        k=await klines(s,sym,tf,100)

        if len(k)<60: return

        close=[float(x[4]) for x in k]

        ma9=ma(close,9)
        ma20=ma(close,20)
        ma50=ma(close,50)

        ma9_now=ma9[-1]
        ma20_now=ma20[-1]
        ma50_now=ma50[-1]

        ma9_prev=ma9[-2]
        ma20_prev=ma20[-2]
        ma50_prev=ma50[-2]

        nome=sym[:-4]

        cor=TF_COLOR.get(tf,"⬜")

        # CRUZAMENTO 9x20 ALTA
        if ma9_now>ma20_now and ma20_now>ma50_now and ma9_prev<=ma20_prev:

            if can_alert(tf,sym):

                msg=(
                    f"{cor} <b>ALERTA {tf.upper()}</b>\n\n"
                    f"<b>SUBINDO</b>\n\n"
                    f"<b>{nome}</b>\n"
                    f"💰 Preço: {p:.6f}\n"
                    f"💵 Volume 24h: ${vol24:,.0f}\n\n"
                    f"⏱️ {now_br()} BR"
                )

                await tg(s,msg)


        # CRUZAMENTO 20x50 ALTA
        if ma20_now>ma50_now and ma9_now>ma50_now and ma20_prev<=ma50_prev:

            if can_alert(tf,sym):

                msg=(
                    f"{cor} <b>ALERTA {tf.upper()}</b>\n\n"
                    f"<b>SUBINDO</b>\n\n"
                    f"<b>{nome}</b>\n"
                    f"💰 Preço: {p:.6f}\n"
                    f"💵 Volume 24h: ${vol24:,.0f}\n\n"
                    f"⏱️ {now_br()} BR"
                )

                await tg(s,msg)


        # CRUZAMENTO 9x20 BAIXA
        if ma9_now<ma20_now and ma20_now<ma50_now and ma9_prev>=ma20_prev:

            if can_alert(tf,sym):

                msg=(
                    f"{cor} <b>ALERTA {tf.upper()}</b>\n\n"
                    f"<b>CAINDO</b>\n\n"
                    f"<b>{nome}</b>\n"
                    f"💰 Preço: {p:.6f}\n"
                    f"💵 Volume 24h: ${vol24:,.0f}\n\n"
                    f"⏱️ {now_br()} BR"
                )

                await tg(s,msg)


        # CRUZAMENTO 20x50 BAIXA
        if ma20_now<ma50_now and ma9_now<ma50_now and ma20_prev>=ma50_prev:

            if can_alert(tf,sym):

                msg=(
                    f"{cor} <b>ALERTA {tf.upper()}</b>\n\n"
                    f"<b>CAINDO</b>\n\n"
                    f"<b>{nome}</b>\n"
                    f"💰 Preço: {p:.6f}\n"
                    f"💵 Volume 24h: ${vol24:,.0f}\n\n"
                    f"⏱️ {now_br()} BR"
                )

                await tg(s,msg)


    except Exception as e:
        print("Erro scan_tf:",e)



async def main_loop():

    async with aiohttp.ClientSession() as s:

        await tg(s,"<b>SCANNER MÉDIAS ATIVO</b>")

        while True:

            try:

                data=await (await s.get(f"{BINANCE}/fapi/v1/ticker/24hr")).json()

                symbols=[
                    d["symbol"] for d in data
                    if d["symbol"].endswith("USDT")
                    and float(d["quoteVolume"])>1_000_000
                    and not any(x in d["symbol"] for x in EXCLUDE)
                ]

                symbols=sorted(
                    symbols,
                    key=lambda x: next((float(t["quoteVolume"]) for t in data if t["symbol"]==x),0),
                    reverse=True
                )[:300]


                tasks=[]

                for sym in symbols:

                    tasks.append(scan_tf(s,sym,"15m"))
                    tasks.append(scan_tf(s,sym,"4h"))
                    tasks.append(scan_tf(s,sym,"1d"))
                    tasks.append(scan_tf(s,sym,"3d"))
                    tasks.append(scan_tf(s,sym,"1w"))
                    tasks.append(scan_tf(s,sym,"1M"))


                await asyncio.gather(*tasks)

            except Exception as e:
                print("Erro main_loop:",e)

            await asyncio.sleep(60)



threading.Thread(target=lambda: asyncio.run(main_loop()),daemon=True).start()

if __name__=="__main__":

    port=int(os.environ.get("PORT") or 10000)
    app.run(host="0.0.0.0",port=port)
