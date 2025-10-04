import os
import json
import requests
from flask import Flask, request, jsonify

app = Flask(name)

# Configurações diretas com os valores fornecidos
TOKEN_DE_TELEGRAM = "8439338131:AAF_BH605VJ3Nnxo8VO3w1eiHBhoV3j6PtE"  # Seu token do Telegram
ID_DO_BATE_PAPO = "-4862798232"  # Seu chat_id
WEBHOOK_SECRET = "12345678"  # Segredo do webhook, igual ao endpoint

@app.route('/webhook/12345678', methods=['POST'])
def webhook():
    try:
        # Log do payload cru para debug, incluindo timestamp atual
        raw_body = request.data.decode('utf-8')
        current_time = "12:16 AM -03, October 04, 2025"
        print(f"Raw body recebido em {current_time}: {raw_body}")

        # Parsear JSON
        dados = request.get_json(silent=True)
        print(f"Parsed data em {current_time}: {dados}")

        if dados and 'moeda' in dados:
            moeda = dados['moeda']
            evento = dados.get('evento', 'não especificado')
            mensagem = f"Alerta do TradingView em {current_time}: Moeda = {moeda}, Evento = {evento}"
        else:
            mensagem = f"Alerta recebido em {current_time}, mas sem dados definidos. Debug: {raw_body or 'vazio'}"

        # Enviar ao Telegram
        url = f"https://api.telegram.org/bot{TOKEN_DE_TELEGRAM}/sendMessage"
        payload = {"chat_id": ID_DO_BATE_PAPO, "text": mensagem}
        resposta = requests.post(url, json=payload)
        if resposta.status_code != 200:
            print(f"Erro Telegram em {current_time}: {resposta.text}")
            return jsonify({"status": "error", "mensagem": "Falha no Telegram"}), 500

        return jsonify({"status": "OK", "mensagem": "Enviado ao Telegram"}), 200
    except Exception as e:
        print(f"Erro no webhook em {current_time}: {e}")
        return jsonify({"status": "error", "mensagem": str(e)}), 500

if name == "main":
    app.run(host="0.0.0.0", port=10000)
