from flask import Flask, jsonify, request
import re
import os
import logging

app = Flask(name)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(name)

@app.route("/webhook", methods=["POST"])
def handle_webhook():
    data = request.get_json()
    if not isinstance(data, dict):
        send_telegram("⚠️ Dados inválidos recebidos do TradingView.")
        return jsonify({"error": "invalid_data"}), 400

    sub = data.get("sub", "")
    interval_raw = data.get("interval", "")

    m = re.search(r'["\']?BINANCE[:/][A-Za-z0-9_/\-]+', sub)
    if not m:
        send_telegram(f"⚠️ Erro: Ticker inválido na mensagem do TradingView. Sub: {sub}")
        return jsonify({"error": "invalid_ticker", "detail": sub}), 400
    tv_ticker = m.group(0)

    exchange, symbol = parse_ticker(tv_ticker)
    if not symbol:
        send_telegram(f"⚠️ Webhook recebido sem ticker válido. Ticker: {tv_ticker}")
        return jsonify({"error": "no_ticker", "detail": tv_ticker}), 400

    if not interval_raw:
        send_telegram("⚠️ Intervalo inválido recebido do TradingView.")
        return jsonify({"error": "invalid_interval"}), 400
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
        klines = get_binance_klines(symbol, interval, limit=max(100, params["srLen"]+20))
    except Exception as e:
        txt = f"⚠️ Erro ao buscar candles Binance para {symbol}: {str(e)}"
        logger.error(txt)
        send_telegram(txt)
        return jsonify({"error": "klines_fail", "detail": str(e)}), 500

    signal, info = identify_signal_from_klines(klines, params)

    if signal == "NONE":
        logger.info(f"No clear signal for {symbol}")
        return jsonify({"status": "no_signal"}), 200

    if signal == "COMPRA":
        msg = f"🟢 *COMPRA* (INÍCIO de alta)\nAtivo: {symbol}\nPreço: {info.get('price')}\nRSI: {info.get('rsi'):.1f}\nIntervalo: {interval}\nFonte: Binance"
    elif signal == "PUMP":
        msg = f"⚡ *PUMP DETECTADO*\nAtivo: {symbol}\nPreço: {info.get('price')}\nVolume (último candle): {info.get('volume')}\nIntervalo: {interval}\nFonte: Binance"
    else:
        msg = f"🔴 *VENDA* (perda de suporte)\nAtivo: {symbol}\nPreço: {info.get('price')}\nRSI: {info.get('rsi'):.1f}\nIntervalo: {interval}\nFonte: Binance"

    send_telegram(msg)
    return jsonify({"status": "ok", "signal": signal}), 200

if name == "main":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
