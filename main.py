import os
import time
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

# --- Carrega variáveis de ambiente ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET")

# --- Verificação de segurança (evita rodar com config errada) ---
if not TELEGRAM_TOKEN or not CHAT_ID or not WEBHOOK_SECRET:
    raise RuntimeError("Erro: variáveis TELEGRAM_TOKEN, CHAT_ID ou WEBHOOK_SECRET não configuradas!")

# --- Função para enviar mensagem ao Telegram ---
def send_telegram_message(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message}

    try:
        response = requests.post(url, json=payload)
        response.raise_for_status()
        time.sleep(0.3)  # pequeno delay para evitar limite do Telegram
    except Exception as e:
        print(f"Erro ao enviar mensagem: {e}")

# --- Endpoint principal para receber alertas do TradingView ---
@app.route('/webhook/<secret>', methods=['POST'])
def webhook(secret):
    if secret != WEBHOOK_SECRET:
        return jsonify({"status": "erro", "msg": "segredo inválido"}), 403

    data = request.get_json()
    if not data:
        return jsonify({"status": "erro", "msg": "JSON inválido"}), 400

    # Verifica se veio o campo 'message' do TradingView
    message = data.get("message", "Alerta recebido do TradingView")

    # Envia para o Telegram
    send_telegram_message(f"📈 Alerta recebido:\n{message}")

    return jsonify({"status": "ok"}), 200

# --- Inicialização padrão ---
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
