import os
import json
import requests
from flask import Flask, request, jsonify

app = Flask(name)

# Pegando variáveis de ambiente do Render
TOKEN_DE_TELEGRAM = os.getenv('TOKEN_DE_TELEGRAM')
ID_DO_BATE_PAPO = os.getenv('ID_DO_CHAT')
WEBHOOK_SECRET = os.getenv('SEGREDO_DO_WEBHOOK')

# Verificação se variáveis existem
if not TOKEN_DE_TELEGRAM or not ID_DO_BATE_PAPO or not WEBHOOK_SECRET:
    raise ValueError("Variáveis de ambiente TELEGRAM_TOKEN, CHAT_ID e WEBHOOK_SECRET precisam estar definidas!")

# Função para enviar mensagem ao Telegram
def enviar_mensagem_de_telegram(mensagem):
    url = f"https://api.telegram.org/bot{TOKEN_DE_TELEGRAM}/sendMessage"
    carga_util = {
        "chat_id": ID_DO_BATE_PAPO,
        "text": mensagem,
        "parse_mode": "Markdown"
    }
    resposta = requests.post(url, json=carga_util)
    if resposta.status_code != 200:
        raise ValueError(f"Erro ao enviar mensagem ao Telegram: {resposta.text}")

@app.route('/webhook/12345678', methods=['POST'])
def webhook():
    try:
        # Log do payload cru para debug
        raw_body = request.data.decode('utf-8')
        print(f"Raw body recebido: {raw_body}")

        # Parsear JSON
        dados = request.get_json(silent=True)
        print(f"Parsed data: {dados}")

        if dados and 'moeda' in dados:
            moeda = dados['moeda']
            evento = dados.get('evento', 'não especificado')
            mensagem = f"Alerta do TradingView: Moeda = {moeda}, Evento = {evento}"
        else:
            mensagem = f"Alerta recebido, mas sem dados definidos. Debug: {raw_body or 'vazio'}"

        enviar_mensagem_de_telegram(mensagem)
        return jsonify({"status": "OK", "mensagem": "Enviado ao Telegram"}), 200
    except Exception as e:
        print(f"Erro no webhook: {e}")
        return jsonify({"status": "error", "mensagem": str(e)}), 500

if name == "main":
    app.run(host="0.0.0.0", port=10000)
