# SCANNER MÉDIAS 9/20/50 COM ALINHAMENTO RECENTE
import os, asyncio, aiohttp, time
from datetime import datetime, timedelta, timezone
from flask import Flask
import threading

app = Flask(__name__)

@app.route("/")
def home():
    return "SCANNER MÉDIAS ATIVO",200

@app.route("/health")
def health():
    return "OK",200


BINANCE="https://api.binance.com"
TELEGRAM_TOKEN=os.getenv("TELEGRAM_TOKEN","").strip()
CHAT_ID=os.getenv("CHAT_ID","").strip()

EXCLUDE=["USDC","USDP","FDUSD","TUSD","USDE","BUSD","DAI","EUR","TRY","BRL"]


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


def ema(data,p):

    if not data: return []

    a=2/(p+1)
    e=data[0]
    out=[e]

    for x in data[1:]:
        e=a*x+(1-a)*e
        out.append(e)

    return out


async def klines(s,sym,tf,lim=100):

    url=f"{BINANCE}/api/v3/klines?symbol={sym}&interval={tf}&limit={lim}"

    async with s.get(url,timeout=10) as r:
        return await r.json() if r.status==200 else []


async def ticker(s,sym):

    url=f"{BINANCE}/api/v3/ticker/24hr?symbol={sym}"

    async with s.get(url,timeout=10) as r:
        return await r.json() if r.status==200 else None



cooldowns={}


def can_alert(tf,sym):

    key=f"{tf}_{sym}"
    cd=cooldowns.get(key,0)

    if tf=="15m": cooldown_time=900
    elif tf=="4h": cooldown_time=7200
    elif tf=="1d": cooldown_time=21600
    else: cooldown_time=86400

    n=time.time()

    if n-cd>=cooldown_time:
        cooldowns[key]=n
        return True

    return False



def alinhado_alta(ma9,ma20,ma50,i):
    return ma9[i]>ma20[i]>ma50[i]


def alinhado_baixa(ma9,ma20,ma50,i):
    return ma9[i]<ma20[i]<ma50[i]



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

        ma9=ema(close,9)
        ma20=ema(close,20)
        ma50=ema(close,50)

        ma9_now=ma9[-1]
        ma9_prev=ma9[-2]

        ma20_now=ma20[-1]
        ma20_prev=ma20[-2]

        # inclinação mínima adicionada
        ma9_up = ma9_now > ma9_prev * 1.0002
        ma20_up = ma20_now > ma20_prev * 1.0001

        ma9_down = ma9_now < ma9_prev * 0.9998
        ma20_down = ma20_now < ma20_prev * 0.9999

        alta_now=alinhado_alta(ma9,ma20,ma50,-1) and ma9_up and ma20_up
        baixa_now=alinhado_baixa(ma9,ma20,ma50,-1) and ma9_down and ma20_down

        if not alta_now and not baixa_now:
            return


        recente=False

        for i in range(2,5):

            if alta_now and not alinhado_alta(ma9,ma20,ma50,-i):
                recente=True

            if baixa_now and not alinhado_baixa(ma9,ma20,ma50,-i):
                recente=True

        if not recente:
            return


        nome=sym[:-4]


        if can_alert(tf,sym):

            if alta_now:
                direcao="🔼 SUBINDO"
            else:
                direcao="🔽 CAINDO"


            titulo=f"<b>📊 ALERTA {tf.upper()}</b>\n\n<b>Alinhamento Recente — {direcao}</b>"


            msg=(
                f"{titulo}\n\n"
                f"<b>{nome}</b>\n"
                f"<b>──────────────────────────</b>\n"
                f"<b>💰 Preço: {p:.6f}</b>\n"
                f"<b>💵 Volume 24h: ${vol24:,.0f}</b>\n"
                f"<b>──────────────────────────</b>\n"
                f"<b>⏱️ {now_br()} BR</b>"
            )

            await tg(s,msg)


    except Exception as e:
        print("Erro scan_tf:",e)



async def main_loop():

    async with aiohttp.ClientSession() as s:

        await tg(s,"<b>SCANNER MÉDIAS ATIVO</b>")

        while True:

            try:

                data=await (await s.get(f"{BINANCE}/api/v3/ticker/24hr")).json()

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
