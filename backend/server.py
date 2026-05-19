"""
server.py — Servidor TCP principal do SockeText.

Responsabilidades:
  - Aceitar conexões TCP de clientes (frontends Flask)
  - Instanciar uma Thread dedicada para cada conexão
  - Fazer broadcast de mensagens para todos os clientes conectados
  - Persistir e carregar histórico diretamente no PostgreSQL
"""

import socket
import threading
import json
import os
import logging

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------

load_dotenv()

HOST = "0.0.0.0"
PORT = int(os.getenv("PORT", 5000))

_DEBUG = os.getenv("DEBUG", "false").lower() == "true"
_DATABASE_URL = os.getenv("EXTERNAL_DATABASE_URL" if _DEBUG else "INTERNAL_DATABASE_URL")

HISTORY_LIMIT = int(os.getenv("HISTORY_LIMIT", 50))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Estado compartilhado entre threads
# ---------------------------------------------------------------------------

# Dicionário {socket: username} de clientes conectados
clients: dict[socket.socket, str] = {}

# Lock para acesso seguro ao dicionário de clientes
clients_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Banco de dados
# ---------------------------------------------------------------------------

def _db_connect() -> psycopg2.extensions.connection:
    """Abre e retorna uma nova conexão com o banco de dados."""
    return psycopg2.connect(_DATABASE_URL)


def save_message(username: str, text: str) -> None:
    """Persiste uma mensagem no banco de dados."""
    sql = "INSERT INTO messages (username, message) VALUES (%s, %s)"
    try:
        with _db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (username, text))
    except psycopg2.Error as e:
        log.error("Erro ao salvar mensagem: %s", e)


def load_history() -> list[dict]:
    """Retorna as últimas HISTORY_LIMIT mensagens em ordem cronológica."""
    sql = """
        SELECT username, message, sent_at
        FROM messages
        ORDER BY sent_at DESC
        LIMIT %s
    """
    try:
        with _db_connect() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, (HISTORY_LIMIT,))
                rows = cur.fetchall()

        return [
            {
                "sender": row["username"],
                "text": row["message"],
                "time": row["sent_at"].strftime("%H:%M"),
            }
            for row in reversed(rows)
        ]
    except psycopg2.Error as e:
        log.error("Erro ao carregar histórico: %s", e)
        return []


# ---------------------------------------------------------------------------
# Broadcast
# ---------------------------------------------------------------------------

def broadcast(payload: dict, exclude: socket.socket | None = None) -> None:
    """
    Envia um payload JSON para todos os clientes conectados.

    Args:
        payload:  Dicionário a enviar (serializado para JSON).
        exclude:  Socket que NÃO deve receber (geralmente o remetente).
    """
    message = (json.dumps(payload) + "\n").encode("utf-8")

    with clients_lock:
        for conn in list(clients):
            if conn is exclude:
                continue
            try:
                conn.sendall(message)
            except OSError:
                log.warning("Falha ao enviar para %s.", clients.get(conn, "?"))


# ---------------------------------------------------------------------------
# Handler de cliente (executa em thread dedicada)
# ---------------------------------------------------------------------------

def handle_client(conn: socket.socket, addr: tuple) -> None:
    """
    Função executada em uma Thread dedicada para cada cliente conectado.

    Protocolo:
      1. Cliente envia JSON: {"type": "join", "username": "<nome>"}
      2. Servidor responde com histórico e notifica demais usuários.
      3. Loop de recepção de mensagens até desconexão.
    """
    username = None
    buffer = ""

    try:
        # --- Handshake ---
        raw = conn.recv(1024).decode("utf-8")
        data = json.loads(raw.strip())

        if data.get("type") != "join":
            log.warning("Handshake inválido de %s.", addr)
            conn.close()
            return

        username = data["username"]
        log.info("'%s' conectou (%s:%s).", username, *addr)

        with clients_lock:
            clients[conn] = username

        # Envia histórico apenas para o novo cliente
        history = load_history()
        conn.sendall(
            (json.dumps({"type": "history", "messages": history}) + "\n").encode("utf-8")
        )

        # Notifica os outros sobre a entrada
        broadcast(
            {"type": "system", "text": f"{username} entrou no chat."},
            exclude=conn,
        )

        # --- Loop principal ---
        while True:
            chunk = conn.recv(4096).decode("utf-8")
            if not chunk:
                break

            buffer += chunk

            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.strip()
                if not line:
                    continue

                msg_data = json.loads(line)
                msg_type = msg_data.get("type")

                if msg_type == "message":
                    text = msg_data.get("text", "")
                    log.info("[%s] %s", username, text)
                    save_message(username, text)
                    broadcast({
                        "type": "message",
                        "sender": username,
                        "text": text,
                    })

                elif msg_type in ("typing", "stop_typing"):
                    broadcast({
                        "type": msg_type,
                        "sender": username,
                    }, exclude=conn)

    except (json.JSONDecodeError, KeyError) as e:
        log.error("Erro de protocolo de '%s': %s", username or addr, e)

    except ConnectionResetError:
        log.info("'%s' desconectou abruptamente.", username or addr)

    finally:
        with clients_lock:
            clients.pop(conn, None)
        conn.close()

        if username:
            log.info("'%s' desconectou.", username)
            broadcast({"type": "system", "text": f"{username} saiu do chat."})


# ---------------------------------------------------------------------------
# Loop de aceitação de conexões
# ---------------------------------------------------------------------------

def accept_loop(server_socket: socket.socket) -> None:
    """Aguarda novas conexões TCP e cria uma Thread para cada uma."""
    log.info("Aguardando conexões na porta %s...", PORT)

    while True:
        try:
            conn, addr = server_socket.accept()
        except OSError:
            break

        thread = threading.Thread(
            target=handle_client,
            args=(conn, addr),
            daemon=True,
            name=f"client-{addr[0]}:{addr[1]}",
        )
        thread.start()
        log.info(
            "Thread '%s' iniciada. Clientes ativos: %d",
            thread.name,
            threading.active_count() - 1,
        )


# ---------------------------------------------------------------------------
# Ponto de entrada
# ---------------------------------------------------------------------------

def main() -> None:
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind((HOST, PORT))
    server_socket.listen(10)

    log.info("Servidor iniciado na porta %s.", PORT)

    try:
        accept_loop(server_socket)
    except KeyboardInterrupt:
        log.info("Encerrando servidor.")
    finally:
        server_socket.close()


if __name__ == "__main__":
    main()
