import os
import json
import logging
from flask import Flask, request, jsonify
import requests
from dotenv import load_dotenv

# carrega .env se existir
load_dotenv()

# logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

# Configurações (use variáveis de ambiente no Render)
CHAT_ID = os.environ.get('CHAT_ID', '-4862798232')
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN', '')
WEBHOOK_SECRET = os.environ.get('WEBHOOK_SECRET', '')

TELEGRAM_API = f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage'

app = Flask(__name__)

if not TELEGRAM_TOKEN:
    logging.warning('TELEGRAM_TOKEN não definido. O envio ao Telegram irá falhar até definir a variável de ambiente.')

if not WEBHOOK_SECRET:
    logging.warning('WEBHOOK_SECRET não definido. O servidor aceitará requisições sem verificação (apenas para testes).')


def build_telegram_text(payload: dict) -> str:
    """Gera o texto que será enviado para o Telegram (HTML)."""
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

    # inclui payload bruto só para debug (opcional)
    try:
        raw = json.dumps(payload, ensure_ascii=False, indent=2)
        text += f"\n<pre>{raw}</pre>"
    except Exception:
        pass

    return text


@app.route('/', methods=['GET'])
def index():
    return jsonify({
        'ok': True,
        'message': 'Servidor ativo. Use POST /webhook/<secret> para enviar alertas (veja README).'
    }), 200


@app.route('/health', methods=['GET'])
def health():
    return jsonify({'up': True}), 200


@app.route('/webhook/<secret>', methods=['POST', 'GET'])
def webhook(secret):
    # valida o segredo pela URL
    if WEBHOOK_SECRET and secret != WEBHOOK_SECRET:
        logging.warning(f'Segredo inválido recebido: {secret}')
        return jsonify({'error': 'invalid secret'}), 401

    # GET -> útil para testar por navegador/ReqBin
    if request.method == 'GET':
        return jsonify({
            'ok': True,
            'message': 'Endpoint /webhook/<secret>: envie um POST JSON com {"symbol":"..."}'
        }), 200

    # POST -> processa o alerta
    payload = request.get_json(force=True, silent=True)
    if not payload:
        logging.warning('Webhook sem JSON ou JSON inválido')
        return jsonify({'error': 'invalid payload'}), 400

    logging.info('Webhook recebido: %s', payload)

    # Constrói mensagem para o Telegram
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
        logging.info('Enviado ao Telegram com sucesso')
    except Exception as e:
        logging.exception('Erro ao enviar mensagem para Telegram: %s', e)
        return jsonify({'error': 'failed to send telegram', 'details': str(e)}), 500

    return jsonify({'ok': True}), 200


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
