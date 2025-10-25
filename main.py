# main.py
# ✅ Estrutura original preservada (Flask + thread + asyncio.run + utils)
# ✅ /health único • Porta 50000 • use_reloader=False (estável no Render)
# ✅ Mensagem única de inicialização no Telegram
# ✅ Corrigido: headers Binance + prevenção de loop infinito e spam

import os, asyncio, aiohttp, time, statistics, threading
from datetime import datetime
from flask import Flask

# ---------------- CONFIG ----------------
BINANCE_HTTP = "https://api.binance.com"
TOP_N = 80
REQ_TIMEOUT = 8

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()

# ---------------- FLASK ----------------
app = Flask(__name__)

@app.route("/")
def home():
    return "✅ Scanner ativo (DualSetup) — Swing 1–3D + SmallCap 10% | 🇧🇷", 200

@app.route("/health")
def health():
    return "OK", 200

# ---------------- UTILS ----------------
def now_br():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S") + " 🇧🇷"

async def tg(aio, text: str):
    if not (TELEGRAM_TOKEN and CHAT_ID):
        print("⚠️ Variáveis TELEGRAM_TOKEN ou CHAT_ID não configuradas!", flush=True)
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
        await aio.post(url, data=payload, timeout=REQ_TIMEOUT)
        print(f"📤 Mensagem enviada ao Telegram: {text[:60]}...", flush=True)
    except Exception as e:
        print(f"⚠️ Erro ao enviar mensagem Telegram: {e}", flush=True)

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
        return [50.0] * len(seq)
    gains, losses = [], []
    for i in range(1, len(seq)):
        diff = seq[i] - seq[i-1]
        gains.append(max(diff, 0))
        losses.append(abs(min(diff, 0)))
    rsi = []
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    rs = avg_gain / (avg_loss + 1e-12)
    rsi.append(100 - (100 / (1 + rs)))
    for i in range(period, len(seq)-1):
        diff = seq[i] - seq[i-1]
        gain = max(diff, 0)
        loss = abs(min(diff, 0))
        avg_gain = (avg_gain * (period-1) + gain) / period
        avg_loss = (avg_loss * (period-1) + loss) / period
        rs = avg_gain / (avg_loss + 1e-12)
        rsi.append(100 - (100 / (1 + rs)))
    return [50.0]*(len(seq)-len(rsi)) + rsi

# ---------------- BINANCE ----------------
async def get_top_usdt_symbols(aio):
    url = f"{BINANCE_HTTP}/api/v3/ticker/24hr"
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; DualSetupBot/1.0; +https://binance.com)",
        "Accept": "application/json"
    }
    try:
        async with aio.get(url, headers=headers, timeout=REQ_TIMEOUT) as r:
            data = await r.json()
            if not isinstance(data, list):
                print(f"⚠️ Retorno inesperado da Binance: {data}", flush=True)
                return []
    except Exception as e:
        print(f"⚠️ Erro ao buscar pares: {e}", flush=True)
        return []

    blocked = (
        "UP","DOWN","BULL","BEAR","BUSD","FDUSD","TUSD","USDC","USD1",
        "USDE","PERP","_PERP","EUR","EURS","CEUR","XUSD","USDX","GUSD"
    )
    pares = []
    for d in data:
        if not isinstance(d, dict): continue
        s = d.get("symbol", "")
        if not s.endswith("USDT"): continue
        if any(x in s for x in blocked): continue
        try:
            qv = float(d.get("quoteVolume", "0") or 0.0)
        except:
            qv = 0.0
        pares.append((s, qv))
    pares.sort(key=lambda x: x[1], reverse=True)
    return [s for s,_ in pares[:TOP_N]]

# ---------------- ALERT STATE ----------------
LAST_HIT = {}
def allowed(symbol, kind, cd_sec):
    ts = LAST_HIT.get((symbol, kind), 0.0)
    return (time.time() - ts) >= cd_sec
def mark(symbol, kind):
    LAST_HIT[(symbol, kind)] = time.time()

# ---------------- WORKER ----------------
async def scan_symbol(aio, symbol):
    try:
        CD_SMALL = 8*60
        CD_SWING = 10*60
        RSI_SMALL_MIN, RSI_SMALL_MAX = 55.0, 80.0
        VOL_SMALL_MIN, VOL_SMALL_MAX = 1.3, 6.0
        RSI_SWING_MIN, RSI_SWING_MAX = 45.0, 60.0
        VOL_SWING_MIN, VOL_SWING_MAX = 0.8, 3.0
        TOL_BB = 0.98
        TOL_EMA = 0.99

        async def get_klines(symbol, interval, limit=210):
            url = f"{BINANCE_HTTP}/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
            headers = {"User-Agent": "Mozilla/5.0"}
            try:
                async with aio.get(url, headers=headers, timeout=REQ_TIMEOUT) as r:
                    data = await r.json()
                    return data if isinstance(data, list) else []
            except:
                return []

        k15 = await get_klines(symbol, "15m")
        k1h = await get_klines(symbol, "1h")
        k4h = await get_klines(symbol, "4h")
        k1d = await get_klines(symbol, "1d")
        if not (len(k15)>=50 and len(k1h)>=50 and len(k4h)>=50 and len(k1d)>=50):
            return

        c15=[float(k[4]) for k in k15]; v15=[float(k[5]) for k in k15]
        ema9_15=ema(c15,9); ema20_15=sma(c15,20)
        u15,m15,l15=bollinger_bands(c15,20,2)
        rsi15=calc_rsi(c15,14)
        vol_ma20_15=sum(v15[-20:])/20.0
        vol_ratio_15=v15[-1]/(vol_ma20_15+1e-12) if vol_ma20_15 else 0.0
        bbw15=(u15[-1]-l15[-1])/(m15[-1]+1e-12)
        bbw15_prev=(u15[-2]-l15[-2])/(m15[-2]+1e-12)
        bb_expand_15 = bbw15 >= bbw15_prev * TOL_BB

        c1h=[float(k[4]) for k in k1h]; v1h=[float(k[5]) for k in k1h]
        ema9_1h=ema(c1h,9); ema20_1h=sma(c1h,20)
        ma50_1h=sma(c1h,50); ma200_1h=sma(c1h,200)
        u1h,m1h,l1h=bollinger_bands(c1h,20,2)
        rsi1h=calc_rsi(c1h,14)
        vol_ma20_1h=sum(v1h[-20:])/20.0
        vol_ratio_1h=v1h[-1]/(vol_ma20_1h+1e-12)
        bbw1h=(u1h[-1]-l1h[-1])/(m1h[-1]+1e-12)
        bbw1h_prev=(u1h[-2]-l1h[-2])/(m1h[-2]+1e-12)
        bb_expand_1h = bbw1h >= bbw1h_prev * TOL_BB

        c4h=[float(k[4]) for k in k4h]
        ema9_4h=ema(c4h,9); ema20_4h=sma(c4h,20)
        ma50_4h=sma(c4h,50); ma200_4h=sma(c4h,200)

        c1d=[float(k[4]) for k in k1d]
        ema20_1d=sma(c1d,20)

        # --- Small Cap
        small_ok = (
            (55 <= rsi15[-1] <= 80)
            and (1.3 <= vol_ratio_15 <= 6.0)
            and (ema9_15[-1] >= ema20_15[-1] * 0.99)
            and bb_expand_15
            and (c1h[-1] >= ema20_1h[-1] * 0.99)
        )
        if small_ok and allowed(symbol, "SMALL", CD_SMALL):
            msg = f"🚨 [EXPLOSÃO] {symbol} | RSI {rsi15[-1]:.1f} | Vol {vol_ratio_15:.1f}x"
            await tg(aio, msg)
            mark(symbol, "SMALL")

    except Exception as e:
        print(f"⚠️ Erro em scan_symbol({symbol}): {e}", flush=True)

# ---------------- MAIN LOOP ----------------
async def main_loop():
    print("🔍 Entrando em main_loop()", flush=True)
    async with aiohttp.ClientSession() as aio:
        symbols = await get_top_usdt_symbols(aio)
        print(f"✅ {len(symbols)} pares obtidos da Binance", flush=True)
        if len(symbols) > 0:
            await tg(aio, f"✅ BOT DUALSETUP INICIADO COM SUCESSO 🚀 | {len(symbols)} pares | {now_br()}")
        else:
            print("⚠️ Nenhum par retornado da Binance! Aguardando 60s antes de tentar novamente...", flush=True)
            await asyncio.sleep(60)
            return
        while True:
            print(f"🔁 Nova varredura iniciada: {now_br()}", flush=True)
            await asyncio.gather(*[scan_symbol(aio, s) for s in symbols])
            await asyncio.sleep(10)

def start_bot():
    print("➡️ Iniciando loop principal...", flush=True)
    while True:
        try:
            asyncio.run(main_loop())
        except Exception as e:
            print(f"⚠️ Erro no loop principal: {e}", flush=True)
            time.sleep(5)

# ---------------- RUN ----------------
if __name__ == "__main__":
    def start_after_ready():
        time.sleep(2)
        print("BOT DUALSETUP INICIADO ✅", flush=True)
        start_bot()

    threading.Thread(target=start_after_ready, daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 50000)), use_reloader=False)
