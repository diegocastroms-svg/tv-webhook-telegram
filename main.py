# main_dualsetup_v2.py
# ✅ Estrutura original preservada (Flask + asyncio + threading + tg)
# ✅ Dois setups: SWING CURTO (1–3D) e SMALL CAP EXPLOSIVA (10%+)
# ✅ Indicadores com faixas flexíveis (RSI, Volume, EMA, MA, BB)
# ✅ Cooldowns revisados: Swing=10min, Small=8min
# ✅ Mensagem de inicialização enviada no deploy

import os, asyncio, aiohttp, time, math, statistics
from datetime import datetime
from flask import Flask
import threading

# ---------------- CONFIG ----------------
BINANCE_HTTP = "https://api.binance.com"
TOP_N = 100
REQ_TIMEOUT = 8

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()

# ---------------- FLASK ----------------
app = Flask(__name__)

@app.route("/")
def home():
    return "✅ Scanner ativo (DualSetup v2) — Swing + SmallCap | 🇧🇷", 200

# ---------------- UTILS ----------------
def now_br():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S") + " 🇧🇷"

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
    out = [seq[0]]
    e = seq[0]
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
        return [50.0]*len(seq)
    gains, losses = [], []
    for i in range(1, len(seq)):
        diff = seq[i]-seq[i-1]
        gains.append(max(diff,0))
        losses.append(abs(min(diff,0)))
    rsi=[]
    avg_gain=sum(gains[:period])/period
    avg_loss=sum(losses[:period])/period
    rs=avg_gain/(avg_loss+1e-12)
    rsi.append(100-(100/(1+rs)))
    for i in range(period,len(seq)-1):
        diff=seq[i]-seq[i-1]
        gain=max(diff,0)
        loss=abs(min(diff,0))
        avg_gain=(avg_gain*(period-1)+gain)/period
        avg_loss=(avg_loss*(period-1)+loss)/period
        rs=avg_gain/(avg_loss+1e-12)
        rsi.append(100-(100/(1+rs)))
    return [50.0]*(len(seq)-len(rsi))+rsi

# ---------------- BINANCE ----------------
async def get_klines(session, symbol, interval, limit=210):
    url=f"{BINANCE_HTTP}/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    try:
        async with session.get(url, timeout=REQ_TIMEOUT) as r:
            data=await r.json()
            if isinstance(data,list): return data
            return []
    except:
        return []

async def get_top_usdt_symbols(session):
    url=f"{BINANCE_HTTP}/api/v3/ticker/24hr"
    async with session.get(url, timeout=REQ_TIMEOUT) as r:
        data=await r.json()
    blocked=("UP","DOWN","BULL","BEAR","BUSD","FDUSD","TUSD","USDC","USD1","USDE","PERP","_PERP","EUR","EURS","CEUR","XUSD","USDX","GUSD")
    pares=[]
    for d in data:
        s=d.get("symbol","")
        if not s.endswith("USDT"): continue
        if any(x in s for x in blocked): continue
        try: qv=float(d.get("quoteVolume","0") or 0.0)
        except: qv=0.0
        pares.append((s,qv))
    pares.sort(key=lambda x:x[1],reverse=True)
    return [s for s,_ in pares[:TOP_N]]

# ---------------- ALERT STATE ----------------
LAST_HIT={}

def can_fire(symbol, kind, cd_sec):
    ts=LAST_HIT.get((symbol,kind),0.0)
    return (time.time()-ts)>=cd_sec

def mark_fire(symbol, kind):
    LAST_HIT[(symbol,kind)]=time.time()

# ---------------- WORKER ----------------
async def scan_symbol(session, symbol):
    try:
        COOLDOWN_SWING=10*60
        COOLDOWN_SMALL=8*60
        RSI_SWING=(45,60)
        VOL_SWING=(0.8,3.0)
        RSI_SMALL=(55,80)
        VOL_SMALL=(1.3,6.0)
        TOL_EMA=0.99
        TOL_MA=0.99
        TOL_BB=0.98

        k15=await get_klines(session,symbol,"15m",limit=210)
        k1h=await get_klines(session,symbol,"1h",limit=210)
        k4h=await get_klines(session,symbol,"4h",limit=210)
        k1d=await get_klines(session,symbol,"1d",limit=210)
        if not (len(k15)>=50 and len(k1h)>=50 and len(k4h)>=50 and len(k1d)>=50): return

        c15=[float(k[4]) for k in k15]
        v15=[float(k[5]) for k in k15]
        ema9_15=ema(c15,9)
        ema20_15=sma(c15,20)
        upper15,mid15,lower15=bollinger_bands(c15,20,2)
        rsi15=calc_rsi(c15,14)
        vol_ma20_15=sum(v15[-20:])/20
        vol_ratio_15=v15[-1]/(vol_ma20_15+1e-12)
        bbw15=(upper15[-1]-lower15[-1])/(mid15[-1]+1e-12)
        bbw15_prev=(upper15[-2]-lower15[-2])/(mid15[-2]+1e-12)
        bb_expand_15=bbw15>=bbw15_prev*TOL_BB

        c1h=[float(k[4]) for k in k1h]
        v1h=[float(k[5]) for k in k1h]
        ema9_1h=ema(c1h,9)
        ema20_1h=sma(c1h,20)
        ma50_1h=sma(c1h,50)
        ma200_1h=sma(c1h,200)
        upper1h,mid1h,lower1h=bollinger_bands(c1h,20,2)
        rsi1h=calc_rsi(c1h,14)
        vol_ma20_1h=sum(v1h[-20:])/20
        vol_ratio_1h=v1h[-1]/(vol_ma20_1h+1e-12)
        bbw1h=(upper1h[-1]-lower1h[-1])/(mid1h[-1]+1e-12)
        bbw1h_prev=(upper1h[-2]-lower1h[-2])/(mid1h[-2]+1e-12)
        bb_expand_1h=bbw1h>=bbw1h_prev*TOL_BB

        c4h=[float(k[4]) for k in k4h]
        ema9_4h=ema(c4h,9)
        ema20_4h=sma(c4h,20)
        ma50_4h=sma(c4h,50)
        ma200_4h=sma(c4h,200)

        c1d=[float(k[4]) for k in k1d]
        ema20_1d=sma(c1d,20)

        # -------- SMALL CAP EXPLOSIVA --------
        i15=len(c15)-1
        if (RSI_SMALL[0]<=rsi15[-1]<=RSI_SMALL[1] and
            VOL_SMALL[0]<=vol_ratio_15<=VOL_SMALL[1] and
            ema9_15[i15]>=ema20_15[i15]*TOL_EMA and
            bb_expand_15 and
            c1h[-1]>ema20_1h[-1]*TOL_EMA and
            can_fire(symbol,"SMALL_ALERT",COOLDOWN_SMALL)):
            price=fmt_price(c15[i15])
            msg=(f"🚨 <b>[EXPLOSÃO SUSTENTÁVEL DETECTADA]</b>\n"
                 f"💥 {symbol}\n"
                 f"🕒 {now_br()}\n"
                 f"💰 Preço: {price}\n"
                 f"📊 Volume: {(vol_ratio_15-1)*100:.0f}% acima da média 💣\n"
                 f"📈 RSI(15m): {rsi15[-1]:.1f} | EMA9>EMA20 ✅ | BB expandindo ✅\n"
                 f"⏱️ Confirmação 1h: Preço > EMA20 ✅\n"
                 f"🔗 https://www.binance.com/en/trade/{symbol}")
            await tg(session,msg)
            mark_fire(symbol,"SMALL_ALERT")

        # -------- SWING CURTO --------
        i1=len(c1h)-1
        i0=i1-1
        cross_9_20_1h=ema9_1h[i0]<=ema20_1h[i0] and ema9_1h[i1]>ema20_1h[i1]
        if (cross_9_20_1h and
            RSI_SWING[0]<=rsi1h[-1]<=RSI_SWING[1] and
            VOL_SWING[0]<=vol_ratio_1h<=VOL_SWING[1] and
            bb_expand_1h and
            ema9_4h[-1]>=ema20_4h[-1]*TOL_EMA and
            ma50_4h[-1]>=ma200_4h[-1]*TOL_MA and
            c1d[-1]>ema20_1d[-1]*TOL_EMA and
            can_fire(symbol,"SWING_ALERT",COOLDOWN_SWING)):
            price=fmt_price(c1h[i1])
            msg=(f"💹 <b>[SWING CURTO – TENDÊNCIA SUSTENTADA]</b>\n"
                 f"📊 {symbol}\n"
                 f"🕒 {now_br()}\n"
                 f"💰 Preço: {price}\n"
                 f"📈 EMA9>EMA20>MA50>MA200 (4h) ✅\n"
                 f"⚡ RSI(1h): {rsi1h[-1]:.1f} | Volume: {(vol_ratio_1h-1)*100:.0f}% acima | BB abrindo ✅\n"
                 f"🧭 Direção 1D: Close > EMA20 ✅\n"
                 f"🔗 https://www.binance.com/en/trade/{symbol}")
            await tg(session,msg)
            mark_fire(symbol,"SWING_ALERT")

    except:
        return

# ---------------- MAIN LOOP ----------------
async def main_loop():
    async with aiohttp.ClientSession() as session:
        symbols=await get_top_usdt_symbols(session)
        await tg(session,f"✅ BOT DUALSETUP INICIADO COM SUCESSO 🚀 | {len(symbols)} pares | {now_br()}")
        if not symbols: return
        print("BOT DUALSETUP INICIADO ✅", flush=True)
        while True:
            tasks=[scan_symbol(session,s) for s in symbols]
            await asyncio.gather(*tasks)
            await asyncio.sleep(10)

def start_bot():
    while True:
        try:
            asyncio.run(main_loop())
        except Exception:
            time.sleep(5)

threading.Thread(target=start_bot,daemon=True).start()
app.run(host="0.0.0.0",port=int(os.getenv("PORT",10001)))
