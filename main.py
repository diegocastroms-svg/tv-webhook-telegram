import os
import json
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

# 🔑 Pegando variáveis de ambiente do Render
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")

# Verificação se variáveis existem
if not TELEGRAM_TOKEN or not CHAT_ID or not WEBHOOK_SECRET:
    raise ValueError("❌ Variáveis de ambiente TELEGRAM_TOKEN, CHAT_ID e WEBHOOK_SECRET precisam estar configuradas!")

# 🚀 Função para enviar mensagem ao Telegram
def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    }
    r = requests.post(url, json=payload)
    return r.json()

# 🌍 Rota inicial só para testar se o servidor está vivo
@app.route("/", methods=["GET"])
def home():
    return "✅ Webhook ativo e funcionando!"

# 🎯 Rota do Webhook com o segredo
@app.route(f"/webhook/{WEBHOOK_SECRET}", methods=["POST"])
def webhook():
    try:
        data = request.json
        print("📩 Recebido:", data)

        # Extrai mensagem recebida do TradingView
        alert_message = data.get("message", "🚨 Alerta recebido, mas sem mensagem definida.")

        # Envia para o Telegram
        send_telegram_message(f"📢 Alerta do TradingView:\n\n{alert_message}")

        return jsonify({"status": "ok", "message": "Enviado ao Telegram"}), 200
    except Exception as e:
        print("❌ Erro no webhook:", e)
        return jsonify({"status": "error", "message": str(e)}), 500

# ▶️ Rodar servidor
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
