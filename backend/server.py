import os
import queue
import threading
from dotenv import load_dotenv
from flask import Flask, request as flask_request
from flask_socketio import SocketIO, emit
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY')

socketio = SocketIO(
    app,
    cors_allowed_origins='*',
    message_queue=os.environ.get('REDIS_URL'),
    async_mode='threading'
)

DEBUG = os.getenv("DEBUG", "false").lower() == "true"
app.config['SQLALCHEMY_DATABASE_URI'] = (
    os.environ.get('EXTERNAL_DATABASE_URL') if DEBUG
    else os.environ.get('INTERNAL_DATABASE_URL')
)
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# Dicionário sid -> {"thread": Thread, "queue": Queue}
# Uma entrada por cliente conectado; removida ao desconectar.
_clients: dict[str, dict] = {}
_clients_lock = threading.Lock()


# ── Sentinel usado para sinalizar encerramento da thread ──────────────────────
_STOP = object()


def _client_worker(sid: str, msg_queue: queue.Queue) -> None:
    """
    Thread exclusiva e persistente para o cliente `sid`.

    Fica bloqueada em msg_queue.get() aguardando trabalho. Cada item
    colocado na fila é um dict com a chave 'type':

        {'type': 'connect'}          — carrega e envia histórico
        {'type': 'message', 'data': {...}}  — processa mensagem recebida
        _STOP                        — encerra a thread (desconexão)
    """
    thread_name = threading.current_thread().name
    print(f'[{thread_name}] Thread iniciada para sid={sid}')

    while True:
        item = msg_queue.get()          # bloqueia até chegar trabalho

        # Sinal de encerramento
        if item is _STOP:
            print(f'[{thread_name}] Recebeu sinal de parada — encerrando.')
            break

        event_type = item.get('type')

        # ── Conexão: carrega histórico ────────────────────────────────────────
        if event_type == 'connect':
            print(f'[{thread_name}] Carregando histórico para sid={sid}...')
            with app.app_context():
                sql_command = text(
                    'SELECT username, sent_at, message FROM messages ORDER BY sent_at'
                )
                result = db.session.execute(sql_command)
                history = [
                    {'sender': row[0], 'time': row[1].strftime('%H:%M'), 'text': row[2]}
                    for row in list(result)
                ]
            print(f'[{thread_name}] Histórico: {len(history)} mensagens')
            socketio.emit('history_load', history, to=sid)

        # ── Mensagem recebida do cliente ──────────────────────────────────────
        elif event_type == 'message':
            data = item['data']
            print(f'[{thread_name}] Processando mensagem: {data}')

            if data['type'] in ('typing', 'stop_typing'):
                # CORREÇÃO: Mudado include_self=False para skip_sid=sid
                socketio.emit('message', data, skip_sid=sid, broadcast=True)
                continue

            with app.app_context():
                sql_command = text(
                    'INSERT INTO messages (username, message) VALUES (:username, :message)'
                )
                db.session.execute(
                    sql_command,
                    {'username': data['sender'], 'message': data['text']}
                )
                db.session.commit()

            # CORREÇÃO: Mudado include_self=False para skip_sid=sid
            socketio.emit('message', data, skip_sid=sid, broadcast=True)

    print(f'[{thread_name}] Thread encerrada.')


# ── SocketIO handlers ─────────────────────────────────────────────────────────

@socketio.on('connect')
def on_connect(auth=None) -> None:
    sid = flask_request.sid

    # Cria fila e thread exclusivas para este cliente
    msg_queue: queue.Queue = queue.Queue()
    t = threading.Thread(
        target=_client_worker,
        args=(sid, msg_queue),
        name=f'client-{sid[:8]}',
        daemon=True
    )

    with _clients_lock:
        _clients[sid] = {'thread': t, 'queue': msg_queue}

    t.start()
    print(f'[on_connect] Thread "{t.name}" iniciada para sid={sid}')

    # Primeiro trabalho: carregar e enviar histórico
    msg_queue.put({'type': 'connect'})


@socketio.on('disconnect')
def on_disconnect() -> None:
    sid = flask_request.sid

    with _clients_lock:
        client = _clients.pop(sid, None)

    if client:
        # Envia sentinel para a thread encerrar limpo
        client['queue'].put(_STOP)
        print(f'[on_disconnect] Sinal de parada enviado para thread "{client["thread"].name}"')


@socketio.on('message')
def handle_message(data: dict) -> None:
    sid = flask_request.sid

    with _clients_lock:
        client = _clients.get(sid)

    if client:
        # Entrega a mensagem na fila da thread do cliente
        client['queue'].put({'type': 'message', 'data': data})
    else:
        print(f'[handle_message] sid={sid} sem thread registrada — mensagem descartada.')


if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=os.getenv('PORT'), allow_unsafe_werkzeug=True)
