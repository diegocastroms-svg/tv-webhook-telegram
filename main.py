import os
import json
import logging
from flask import Flask, request, jsonify
import requests

# Configurações via variáveis de ambiente
CHAT_ID = os.environ.get('CHAT_ID', '-4862798232')
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN', '8439338131:AAF_BH605VJ3Nnxo8VO3w1eiHBhoV3j6PtE')
WEBHOOK_SECRET = os.environ.get('WEBHOOK_SECRET', '12345678')

TELEGRAM_API = f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage'

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)


def verify_secret(req):
    """Verifica o segredo vindo no header ou no campo JSON 'secret'"""
    header_secret = req.headers.get('X-Webhook-Secret') or req.headers.get('X-Webhook-Token')
    if header_secret:
        return header_secret == WEBHOOK_SECRET

    try:
        data = req.get_json(force=True, silent=True) or {}
    except Exception:
        data = {}

    payload_secret = data.get('secret')
    if payload_secret:
        return str(payload_secret) == str(WEBHOOK_SECRET)

    return False


def build_telegram_text(payload: dict) -> str:
    """Mensagem para o Telegram (HTML parse)"""
    symbol = payload.get('symbol', payload.get('ticker', 'N/A'))
    condition = payload.get('condition', payload.get('reason', 'trigger'))
    price = payload.get('price', payload.get('close', 'N/A'))
    vol = payload.get('volume', 'N/A')
    time = payload.get('time', '')

    text = (
        f"<b>🔔 ALERTA</b>\n"
        f"<b>Ativo:</b> {symbol}\n"
        f"<b>Condição:</b> {condition}\n"
        f"<b>Preço:</b> {price}\n"
        f"<b>Volume:</b> {vol}\n"
    )

    if time:
        text += f"<b>Hora:</b> {time}\n"

    try:
        raw = json.dumps(payload, ensure_ascii=False, indent=2)
        text += f"\n<pre>{raw}</pre>"
    except Exception:
        pass

    return text


@app.route('/health', methods=['GET'])
def health():
    return jsonify({'up': True}), 200


@app.route('/webhook', methods=['POST'])
def webhook():
    if not verify_secret(request):
        logging.warning('Webhook recebido com segredo inválido')
        return jsonify({'error': 'invalid secret'}), 401

    payload = request.get_json(force=True, silent=True)
    if not payload:
        logging.warning('Webhook sem JSON')
        return jsonify({'error': 'invalid payload'}), 400

    logging.info('Webhook recebido: %s', payload)

    text = build_telegram_text(payload)

    data = {
        'chat_id': CHAT_ID,
        'text': text,
        'parse_mode': 'HTML',
        'disable_web_page_preview': True,
    }

    try:
        resp = requests.post(TELEGRAM_API, json=data, timeout=10)
        resp.raise_for_status()
    except Exception as e:
        logging.exception('Erro ao enviar mensagem para Telegram: %s', e)
        return jsonify({'error': 'failed to send telegram', 'details': str(e)}), 500

    return jsonify({'ok': True}), 200


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
