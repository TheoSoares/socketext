from flask import Flask
from flask_socketio import SocketIO, emit
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text
from dotenv import load_dotenv
import threading
import os

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY')

# async_mode='threading' é obrigatório para usar threads reais do SO.
# Com 'gevent' o servidor usa greenlets (cooperativos), não threads — o
# requisito exige uma threading.Thread por conexão.
socketio = SocketIO(
    app,
    cors_allowed_origins='*',
    message_queue=os.environ.get('REDIS_URL'),
    async_mode='threading'          # <- alterado de 'gevent'
)

DEBUG = os.getenv("DEBUG", "false").lower() == "true"
app.config['SQLALCHEMY_DATABASE_URI'] = (
    os.environ.get('EXTERNAL_DATABASE_URL') if DEBUG
    else os.environ.get('INTERNAL_DATABASE_URL')
)
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# Dicionário que mapeia sid -> Thread para fins de log/auditoria.
# O Flask-SocketIO já despacha cada handler em sua própria thread quando
# async_mode='threading', mas instanciamos e registramos aqui
# explicitamente para satisfazer o requisito.
_client_threads: dict[str, threading.Thread] = {}
_threads_lock = threading.Lock()


def _handle_connect(sid: str) -> None:
    """
    Função executada dentro da thread dedicada ao cliente `sid`.
    Busca o histórico e emite para o cliente que acabou de conectar.
    """
    print(f'[Thread {threading.current_thread().name}] Nova conexão sid={sid} — enviando histórico...')
    with app.app_context():
        sql_command = text('SELECT username, sent_at, message FROM messages ORDER BY sent_at')
        result = db.session.execute(sql_command)
        new_result = [
            {'sender': row[0], 'time': row[1].strftime('%H:%M'), 'text': row[2]}
            for row in list(result)
        ]
    print(f'[Thread {threading.current_thread().name}] Histórico: {len(new_result)} mensagens')
    socketio.emit('history_load', new_result, to=sid)


# <-::- SocketIO Configs -::->

@socketio.on('connect')
def on_connect(auth=None) -> None:
    from flask import request as flask_request
    sid = flask_request.sid  # identificador único da conexão

    # Instancia uma thread exclusiva para este cliente.
    t = threading.Thread(
        target=_handle_connect,
        args=(sid,),
        name=f'client-{sid[:8]}',
        daemon=True
    )

    with _threads_lock:
        _client_threads[sid] = t

    t.start()
    print(f'[on_connect] Thread "{t.name}" iniciada para sid={sid}')


@socketio.on('disconnect')
def on_disconnect() -> None:
    from flask import request as flask_request
    sid = flask_request.sid
    with _threads_lock:
        thread = _client_threads.pop(sid, None)
    name = thread.name if thread else sid[:8]
    print(f'[on_disconnect] Cliente desconectado — thread "{name}" encerrada (daemon)')


@socketio.on('message')
def handle_message(data: dict) -> None:
    print(f'[Thread {threading.current_thread().name}] Dados recebidos: {data}')

    if data['type'] in ('typing', 'stop_typing'):
        emit('message', data, include_self=False, broadcast=True)
        return

    sql_command = text(
        'INSERT INTO messages (username, message) VALUES (:username, :message)'
    )
    db.session.execute(sql_command, {'username': data['sender'], 'message': data['text']})
    db.session.commit()

    emit('message', data, include_self=False, broadcast=True)


if __name__ == '__main__':
    # allow_unsafe_werkzeug não é necessário com async_mode='threading',
    # mas é mantido para compatibilidade com execução local.
    socketio.run(app, host='0.0.0.0', port=os.getenv('PORT'), allow_unsafe_werkzeug=True)
