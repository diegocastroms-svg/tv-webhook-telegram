# main_long.py — V24 ALIGNMENT CROSS MULTI TF COLOR
import os, asyncio, aiohttp, time
from datetime import datetime, timedelta, timezone
from flask import Flask
import threading

app = Flask(__name__)

@app.route("/")
def home():
    return "V24 ALIGNMENT CROSS MULTI TF", 200

@app.route("/health")
def health():
    return "OK", 200


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


def rsi(prices,p=14):
    if len(prices)<p+1:return 50
    d=[prices[i]-prices[i-1] for i in range(1,len(prices))]
    g=[max(x,0) for x in d[-p:]]
    l=[abs(min(x,0)) for x in d[-p:]]
    ag=sum(g)/p
    al=sum(l)/p or 1e-12
    return 100-100/(1+ag/al)


async def klines(s,sym,tf,lim=250):
    url=f"{BINANCE}/api/v3/klines?symbol={sym}&interval={tf}&limit={lim}"
    async with s.get(url,timeout=10) as r:
        return await r.json() if r.status==200 else []


async def ticker(s,sym):
    url=f"{BINANCE}/api/v3/ticker/24hr?symbol={sym}"
    async with s.get(url,timeout=10) as r:
        return await r.json() if r.status==200 else None


cooldowns={}

cooldown_times={
"15m":900,
"4h":7200,
"1d":21600,
"3d":86400,
"1w":86400,
"1M":86400
}


icons={
"15m":"🟢",
"4h":"🔵",
"1d":"🟣",
"3d":"🟠",
"1w":"🔴",
"1M":"🏆"
}


def can_alert(tf,sym):

    n=time.time()
    key=f"{sym}_{tf}"
    cd=cooldown_times.get(tf,3600)

    if n-cooldowns.get(key,0)>=cd:
        cooldowns[key]=n
        return True

    return False



async def scan_tf(s,sym,tf):

    try:

        t=await ticker(s,sym)
        if not t:return

        p=float(t["lastPrice"])
        vol24=float(t["quoteVolume"])

        if vol24<5_000_000:return
        if any(x in sym for x in EXCLUDE):return


        k=await klines(s,sym,tf,250)
        if len(k)<60:return

        close=[float(x[4]) for x in k]


        ma9_prev=sum(close[-10:-1])/9
        ma20_prev=sum(close[-21:-1])/20
        ma50_prev=sum(close[-51:-1])/50

        ma9_now=sum(close[-9:])/9
        ma20_now=sum(close[-20:])/20
        ma50_now=sum(close[-50:])/50


        alta_antes=ma9_prev>ma20_prev>ma50_prev
        alta_agora=ma9_now>ma20_now>ma50_now

        baixa_antes=ma9_prev<ma20_prev<ma50_prev
        baixa_agora=ma9_now<ma20_now<ma50_now


        formou_alta=(not alta_antes) and alta_agora
        formou_baixa=(not baixa_antes) and baixa_agora


        if not(formou_alta or formou_baixa):
            return


        direcao="🔼 SUBINDO" if formou_alta else "🔽 CAINDO"

        current_rsi=rsi(close)

        if current_rsi<40 or current_rsi>80:
            return


        stop=min(float(x[3]) for x in k[-10:])*0.98
        alvo1=p*1.08
        alvo2=p*1.15

        nome=sym[:-4]


        if can_alert(tf,sym):

            icon=icons.get(tf,"🌕")

            titulo=f"<b>{icon} ALERTA {tf.upper()} 🔶</b>\n\n<b>Alinhamento Recém-Formado — {direcao}</b>"

            msg=(
            f"{titulo}\n\n"
            f"<b>{nome}</b>\n"
            f"<b>──────────────────────────</b>\n"
            f"<b>💰 Preço: {p:.6f}</b>\n"
            f"<b>📈 RSI: {current_rsi:.1f}</b>\n"
            f"<b>💵 Volume 24h: ${vol24:,.0f}</b>\n"
            f"<b>──────────────────────────</b>\n"
            f"<b>🛑 Stop: {stop:.6f}</b>\n"
            f"<b>🎯 +8%: {alvo1:.6f}</b>\n"
            f"<b>🏁 +15%: {alvo2:.6f}</b>\n"
            f"<b>──────────────────────────</b>\n"
            f"<b>⏱️ {now_br()} BR</b>"
            )

            await tg(s,msg)

    except Exception as e:
        print("Erro scan_tf:",e)



TIMEFRAMES=[
"15m",
"4h",
"1d",
"3d",
"1w",
"1M"
]



async def main_loop():

    async with aiohttp.ClientSession() as s:

        await tg(s,"<b>V24 ALIGNMENT CROSS MULTI TF</b>")

        while True:

            try:

                data=await (await s.get(f"{BINANCE}/api/v3/ticker/24hr")).json()

                symbols=[
                d["symbol"]
                for d in data
                if d["symbol"].endswith("USDT")
                and float(d["quoteVolume"])>1_000_000
                and not any(x in d["symbol"] for x in EXCLUDE)
                ]

                symbols=sorted(
                symbols,
                key=lambda x:next((float(t["quoteVolume"]) for t in data if t["symbol"]==x),0),
                reverse=True
                )[:300]


                tasks=[]

                for sym in symbols:
                    for tf in TIMEFRAMES:
                        tasks.append(scan_tf(s,sym,tf))

                await asyncio.gather(*tasks)


            except Exception as e:
                print("Erro main_loop:",e)


            await asyncio.sleep(60)



threading.Thread(target=lambda:asyncio.run(main_loop()),daemon=True).start()


if __name__=="__main__":

    port=int(os.environ.get("PORT") or 10000)

    app.run(host="0.0.0.0",port=port)

