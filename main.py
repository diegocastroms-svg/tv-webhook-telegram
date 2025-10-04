import os
import time
import requests
from flask import Flask, request, jsonify, redirect

app = Flask(__name__)

# --- Carrega variáveis de ambiente ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET")

# --- Verificação de segurança ---
if not TELEGRAM_TOKEN or not CHAT_ID or not WEBHOOK_SECRET:
    raise RuntimeError("Erro: variáveis TELEGRAM_TOKEN, CHAT_ID ou WEBHOOK_SECRET não configuradas!")

# --- Função para enviar mensagem ao Telegram ---
def send_telegram_message(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message}
    try:
        response = requests.post(url, json=payload)
        response.raise_for_status()
        time.sleep(0.4)
        print("[OK] Mensagem enviada ao Telegram")
    except Exception as e:
        print(f"Erro ao enviar mensagem: {e}")

# --- Função auxiliar para formatar o par corretamente ---
QUOTES = ["USDT","USDC","FDUSD","BUSD","TUSD","DAI","TRY","BRL","EUR","BTC","ETH","BNB"]
def to_binance_pair(symbol: str) -> str:
    s = (symbol or "").upper().replace("-", "").replace("/", "")
    for q in QUOTES:
        if s.endswith(q) and len(s) > len(q):
            base = s[:-len(q)]
            return f"{base}_{q}"
    return f"{s[:-4]}_{s[-4:]}" if len(s) > 4 else s

# --- Rota principal para receber alertas do TradingView ---
@app.route('/webhook/<secret>', methods=['POST'])
def webhook(secret):
    if secret != WEBHOOK_SECRET:
        return jsonify({"status": "erro", "msg": "segredo inválido"}), 403

    data = request.get_json()
    if not data:
        return jsonify({"status": "erro", "msg": "JSON inválido"}), 400

    try:
        symbol = data.get("symbol", "—")
        price = data.get("price", "—")
        volume = data.get("volume", "—")
        condition = data.get("condition", "—")
        time_alert = data.get("time", "—")

        pair = to_binance_pair(symbol)
        link = f"https://www.binance.com/en/trade/{pair}?type=spot"

        message = (
            f"🔔 ALERTA\n"
            f"Ativo: {symbol}\n"
            f"Condição: {condition}\n"
            f"Preço: {price}\n"
            f"Volume: {volume}\n"
            f"Hora: {time_alert}\n\n"
            f"📊 Abrir gráfico: {link}"
        )

        send_telegram_message(message)
        print(f"[OK] Alerta enviado: {symbol}")

        return jsonify({"status": "ok"}), 200

    except Exception as e:
        print(f"Erro ao processar alerta: {e}")
        return jsonify({"status": "erro"}), 500

# --- Rota de fallback (se alguém acessar /open/<symbol>) ---
@app.route('/open/<symbol>')
def open_in_web(symbol):
    pair = to_binance_pair(symbol)
    return redirect(f"https://www.binance.com/en/trade/{pair}?type=spot", code=302)

# --- Inicialização ---
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
