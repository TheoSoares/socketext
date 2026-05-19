"""
server.py — Servidor principal do SockeText.

Responsabilidades:
  - Aceitar conexões WebSocket de browsers via Flask-SocketIO.
  - Usar Redis como message_queue para sincronizar múltiplas instâncias:
    mensagens emitidas em um servidor chegam a clientes conectados nos demais.
  - Gerenciar threads manualmente: uma Thread dedicada por cliente conectado,
    responsável por carregar histórico e processar mensagens sem bloquear
    o loop principal do SocketIO.
  - Persistir e carregar histórico via PostgreSQL.
"""

import os
import threading
import logging

from flask import Flask, request
from flask_socketio import SocketIO, emit
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY")

# Redis como message_queue: garante que mensagens emitidas em uma instância
# sejam entregues a clientes conectados em outras instâncias do servidor.
# async_mode="threading" substitui gevent — threads gerenciadas manualmente.
socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    message_queue=os.environ.get("REDIS_URL"),
    async_mode="threading",
)

DEBUG = os.getenv("DEBUG", "false").lower() == "true"
app.config["SQLALCHEMY_DATABASE_URI"] = (
    os.environ.get("EXTERNAL_DATABASE_URL")
    if DEBUG
    else os.environ.get("INTERNAL_DATABASE_URL")
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

# ---------------------------------------------------------------------------
# Banco de dados
# ---------------------------------------------------------------------------

def load_history() -> list[dict]:
    """Retorna todas as mensagens do banco em ordem cronológica."""
    sql = text("SELECT username, sent_at, message FROM messages ORDER BY sent_at")
    with app.app_context():
        result = db.session.execute(sql)
        return [
            {
                "sender": row[0],
                "time":   row[1].strftime("%H:%M"),
                "text":   row[2],
            }
            for row in result
        ]


def save_message(username: str, message: str) -> None:
    """Persiste uma mensagem no banco de dados."""
    sql = text("INSERT INTO messages (username, message) VALUES (:username, :message)")
    try:
        with app.app_context():
            db.session.execute(sql, {"username": username, "message": message})
            db.session.commit()
    except Exception as e:
        log.error("Erro ao salvar mensagem: %s", e)


# ---------------------------------------------------------------------------
# Threads dedicadas
# ---------------------------------------------------------------------------

def client_setup_thread(sid: str) -> None:
    """
    Thread dedicada ao setup de um novo cliente.

    Carrega o histórico do banco (I/O potencialmente lento) e o envia
    apenas para o cliente recém-conectado, sem bloquear o loop principal.

    Args:
        sid: ID da sessão SocketIO do cliente.
    """
    log.info("Thread de setup iniciada para sid '%s'.", sid[:8])
    history = load_history()
    socketio.emit("history_load", history, to=sid)
    log.info("Histórico enviado para sid '%s' (%d msgs).", sid[:8], len(history))


def handle_message_thread(data: dict) -> None:
    """
    Thread dedicada ao processamento de uma mensagem recebida.

    Separa persistência e broadcast do loop de eventos, evitando que
    operações de I/O (banco) bloqueiem outros clientes.

    Args:
        data: Dicionário com os dados da mensagem recebida.
    """
    msg_type = data.get("type")

    if msg_type in ("typing", "stop_typing"):
        # Repassa indicador de digitação sem persistir
        socketio.emit("message", data, include_self=False)
        return

    if msg_type == "message":
        username = data.get("sender", "")
        message  = data.get("text", "")
        log.info("[%s] %s", username, message)
        save_message(username, message)
        # O Redis message_queue garante entrega às demais instâncias.
        socketio.emit("message", data, include_self=False, broadcast=True)


# ---------------------------------------------------------------------------
# Eventos SocketIO
# ---------------------------------------------------------------------------

@socketio.on("connect")
def on_connect(auth=None) -> None:
    """
    Novo cliente conectou.

    Instancia uma Thread dedicada para carregar e enviar o histórico,
    mantendo o loop de eventos livre para outros clientes.
    """
    sid = request.sid
    log.info("Nova conexão: sid '%s'. Iniciando thread de setup.", sid[:8])

    t = threading.Thread(
        target=client_setup_thread,
        args=(sid,),
        daemon=True,
        name=f"setup-{sid[:8]}",
    )
    t.start()


@socketio.on("message")
def on_message(data: dict) -> None:
    """
    Recebe uma mensagem do cliente e despacha para thread dedicada.
    """
    t = threading.Thread(
        target=handle_message_thread,
        args=(data,),
        daemon=True,
        name=f"msg-{data.get('sender', '?')[:8]}",
    )
    t.start()


# ---------------------------------------------------------------------------
# Ponto de entrada
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    log.info("Servidor iniciado na porta %s.", port)
    socketio.run(app, host="0.0.0.0", port=port, allow_unsafe_werkzeug=True)
