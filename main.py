import os
import requests
import json
import html
from flask import Flask, request, jsonify

app = Flask(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET")

def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}
    requests.post(url, json=payload)

@app.route("/", methods=["GET"])
def index():
    return "OK - Webhook ativo"

@app.route("/webhook/<secret>", methods=["POST"])
def webhook(secret):
    if secret != WEBHOOK_SECRET:
        return jsonify({"error": "invalid secret"}), 403

    data = request.get_json(silent=True)
    if data and "message" in data:
        message = f"🚀 Alerta TradingView\n{data['message']}"
    else:
        message = f"🚀 Alerta TradingView\n{request.data.decode('utf-8')}"

    send_telegram(message)
    return jsonify({"status":"ok"})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
