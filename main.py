# main.py
import os
import json
import math
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

# ENV variables (defina no Render)
TELEGRAM_TOKEN = os.environ.get( "8439338131:AAF_BH605VJ3Nnxo8VO3w1eiHBhoV3j6PtE" )
CHAT_ID = os.environ.get("-4862798232")
WEBHOOK_SECRET = os.environ.get("12345678")  # nome que você usou no Render

# ---------- helpers para indicadores (simples, sem bibliotecas externas) ----------
def sma(arr, period):
    if len(arr) < period:
        return sum(arr) / max(len(arr),1)
    return sum(arr[-period:]) / period

def ema_series(values, period):
    """Retorna lista com EMA (mesmo tamanho de values)."""
    if not values:
        return []
    k = 2.0 / (period + 1.0)
    ema = []
    if len(values) >= period:
        s = sum(values[:period]) / period
        ema = [None]*(period-1)
        ema.append(s)
        for v in values[period:]:
            s = (v - s) * k + s
            ema.append(s)
    else:
        s = values[0]
        ema.append(s)
        for v in values[1:]:
            s = (v - s) * k + s
            ema.append(s)
    return ema

def latest_ema(values, period):
    em = ema_series(values, period)
    return em[-1] if em else None

def rsi_latest(closes, period=14):
    if len(closes) < period + 1:
        return None
    gains = []
    losses = []
    for i in range(1, len(closes)):
        delta = closes[i] - closes[i-1]
        gains.append(delta if delta > 0 else 0.0)
        losses.append(-delta if delta < 0 else 0.0)
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    for i in range(period, len(gains)):
        gain = gains[i]
        loss = losses[i]
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        if avg_loss == 0:
            rsi = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))
    return rsi

def parabolic_sar(highs, lows, af_start=0.02, af_step=0.02, af_max=0.2):
    n = len(highs)
    if n == 0:
        return [], []
    sar = [0.0] * n
    is_up = [True] * n
    sar[0] = lows[0] - (highs[0] - lows[0])
    up = True
    ep = highs[0]
    af = af_start
    for i in range(1, n):
        prev_sar = sar[i-1]
        new_sar = prev_sar + af * (ep - prev_sar)
        if up:
            if new_sar > min(lows[i-1], lows[i-2] if i-2>=0 else lows[i-1]):
                new_sar = min(lows[i-1], lows[i-2] if i-2>=0 else lows[i-1])
        else:
            if new_sar < max(highs[i-1], highs[i-2] if i-2>=0 else highs[i-1]):
                new_sar = max(highs[i-1], highs[i-2] if i-2>=0 else highs[i-1])
        if up:
            if lows[i] < new_sar:
                up = False
                new_sar = ep
                ep = lows[i]
                af = af_start
            else:
                if highs[i] > ep:
                    ep = highs[i]
                    af = min(af + af_step, af_max)
        else:
            if highs[i] > new_sar:
                up = True
                new_sar = ep
                ep = highs[i]
                af = af_start
            else:
                if lows[i] < ep:
                    ep = lows[i]
                    af = min(af + af_step, af_max)
        sar[i] = new_sar
        is_up[i] = up
    return sar, is_up

# ---------- Binance public klines ----------
def map_interval(tv_interval):
    if not tv_interval:
        return "3m"
    s = str(tv_interval).lower()
    if s.isdigit():
        return s + "m"
    if any(x in s for x in ['m','h','d','w','M']):
        return s
    return s + "m"

def parse_ticker(tv_ticker):
    if not tv_ticker:
        return None, None
    t = tv_ticker
    if ":" in t:
        t = t.split(":")[1]
    t = t.replace("/", "").replace("-", "")
    return "BINANCE", t.upper()

def get_binance_klines(symbol, interval, limit=200):
    url = "https://api.binance.com/api/v3/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    r = requests.get(url, params=params, timeout=10)
    if r.status_code != 200:
        raise Exception(f"Binance klines error {r.status_code}: {r.text}")
    data = r.json()
    closes = [float(c[4]) for c in data]
    opens  = [float(c[1]) for c in data]
    highs  = [float(c[2]) for c in data]
    lows   = [float(c[3]) for c in data]
    vols   = [float(c[5]) for c in data]
    return {"opens": opens, "closes": closes, "highs": highs, "lows": lows, "vols": vols}

# ---------- logic to identify signal ----------
def identify_signal_from_klines(klines, params):
    closes = klines["closes"]
    opens  = klines["opens"]
    highs  = klines["highs"]
    lows   = klines["lows"]
    vols   = klines["vols"]

    n = len(closes)
    if n < max(30, params["srLen"] + 5):
        return None, "NOT_ENOUGH_DATA"

    emaFast = ema_series(closes, params["emaFastLen"])
    emaSlow = ema_series(closes, params["emaSlowLen"])
    if len(emaFast) < 2 or len(emaSlow) < 2:
        return None, "NO_EMA"
    emaFast_prev = emaFast[-2] if emaFast[-2] is not None else emaFast[-1]
    emaFast_curr = emaFast[-1]
    emaSlow_prev = emaSlow[-2] if emaSlow[-2] is not None else emaSlow[-1]
    emaSlow_curr = emaSlow[-1]

    rsi_val = rsi_latest(closes, params["rsiLen"])
    sar_list, is_up_list = parabolic_sar(highs, lows)
    sar_curr = sar_list[-1] if sar_list else None
    volMA = sma(vols, params["volMALen"])
    support = min(lows[-params["srLen"]:])
    resistance = max(highs[-params["srLen"]:])
    o = opens[-1]
    c = closes[-1]
    v = vols[-1]

    bullCross = (emaFast_prev is not None and emaSlow_prev is not None and emaFast_prev < emaSlow_prev and emaFast_curr > emaSlow_curr)
    bearCross = (emaFast_prev is not None and emaSlow_prev is not None and emaFast_prev > emaSlow_prev and emaFast_curr < emaSlow_curr)

    buySignal = bullCross and (rsi_val is not None and rsi_val > params["rsiBuyLevel"]) and (v > volMA * params["volFactor"]) and (sar_curr is not None and c > sar_curr) and (c > support)
    sellSignal = (c < support) and (rsi_val is not None and rsi_val < 45) and (c < sar_curr if sar_curr is not None else True) and (v > volMA)
    pumpSignal = ((c - o) / (o if o!=0 else 1e-12) >= params["pumpPct"]) and (v > volMA * params["pumpVolMul"]) and (c > resistance)

    if buySignal:
        return "COMPRA", {"price": c, "rsi": rsi_val}
    if pumpSignal:
        return "PUMP", {"price": c, "volume": v}
    if sellSignal:
        return "VENDA", {"price": c, "rsi": rsi_val}
    return "NONE", {}

# ---------- send to telegram ----------
def send_telegram(text):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("TELEGRAM_TOKEN or CHAT_ID not configured")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}
    try:
        r = requests.post(url, json=payload, timeout=10)
        return r.status_code == 200
    except Exception as e:
        print("Telegram send error:", e)
        return False

# ---------- webhook endpoint ----------
@app.route("/", methods=["GET"])
def index():
    return "OK - Webhook ativo"

@app.route("/webhook/<secret>", methods=["POST"])
def webhook(secret):
    if not WEBHOOK_SECRET or secret != WEBHOOK_SECRET:
        return jsonify({"error": "invalid secret"}), 403

    data = None
    try:
        data = request.get_json(force=False, silent=True)
        if data is None:
            raw = request.data.decode("utf-8", errors="ignore")
            data = json.loads(raw) if raw else {}
    except Exception as e:
        print("parse error:", e)
        data = {}

    tv_ticker = data.get("ticker") or data.get("symbol") or data.get("pair") or ""
    interval_raw = data.get("interval") or "3m"
    if not tv_ticker:
        raw = request.data.decode("utf-8", errors="ignore")
        if "ticker" in raw and ":" in raw:
            try:
                start = raw.index("ticker")
                sub = raw[start: start+200]
                import re
                m = re.search(r'["\']?BINANCE[:/][A-Za-z0-9_/\-]+', sub)
                if m:
                    tv_ticker = m.group(0)
            except:
                pass

    exchange, symbol = parse_ticker(tv_ticker)
    if not symbol:
        send_telegram("⚠️ Webhook recebido sem ticker válido. Verifique a mensagem do alerta do TradingView.")
        return jsonify({"error": "no ticker"}), 400

    interval = map_interval(interval_raw)

    params = {
        "emaFastLen": 9,
        "emaSlowLen": 21,
        "rsiLen": 14,
        "rsiBuyLevel": 55,
        "volMALen": 20,
        "volFactor": 2.0,
        "pumpVolMul": 2.5,
        "pumpPct": float(data.get("pump_pct", 0.20)),
        "srLen": 20
    }

    try:
        klines = get_binance_klines(symbol, interval, limit= max(100, params["srLen"]+20))
    except Exception as e:
        txt = f"⚠️ Erro ao buscar candles Binance para {symbol}: {str(e)}"
        print(txt)
        send_telegram(txt)
        return jsonify({"error": "klines_fail", "detail": str(e)}), 500

    signal, info = identify_signal_from_klines(klines, params)

    if signal == "NONE":
        print(f"No clear signal for {symbol}")
        return jsonify({"status": "no_signal"}), 200

    if signal == "COMPRA":
        msg = f"🟢 *COMPRA* (INÍCIO de alta)\nAtivo: {symbol}\nPreço: {info.get('price')}\nRSI: {info.get('rsi'):.1f}\nIntervalo: {interval}\nFonte: Binance"
    elif signal == "PUMP":
        msg = f"⚡ *PUMP DETECTADO*\nAtivo: {symbol}\nPreço: {info.get('price')}\nVolume (último candle): {info.get('volume')}\nIntervalo: {interval}\nFonte: Binance"
    else:
        msg = f"🔴 *VENDA* (perda de suporte)\nAtivo: {symbol}\nPreço: {info.get('price')}\nRSI: {info.get('rsi'):.1f}\nIntervalo: {interval}\nFonte: Binance"

    send_telegram(msg)
    return jsonify({"status":"ok","signal":signal}), 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

