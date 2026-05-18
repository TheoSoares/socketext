"""
server.py — Servidor TCP principal do SockeText.

Responsabilidades:
  - Aceitar conexões TCP de clientes (frontends Flask)
  - Instanciar uma Thread dedicada para cada conexão
  - Fazer broadcast de mensagens para todos os clientes conectados
  - Persistir e carregar histórico via db.py
  - Participar do mecanismo de replicação via replication.py
"""

import socket
import threading
import json
import os
import logging

from dotenv import load_dotenv

from db import save_message, load_history
from replication import ReplicationManager

# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------

load_dotenv()

HOST = "0.0.0.0"
PORT = int(os.getenv("PORT", 5000))
ROLE = os.getenv("ROLE", "primary")  # "primary" ou "replica"
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

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
# Broadcast
# ---------------------------------------------------------------------------

def broadcast(payload: dict, exclude: socket.socket | None = None) -> None:
    """
    Envia um payload JSON para todos os clientes conectados.

    Args:
        payload:  Dicionário com os dados a enviar (será serializado para JSON).
        exclude:  Socket que NÃO deve receber a mensagem (geralmente o remetente).
    """
    message = json.dumps(payload) + "\n"
    encoded = message.encode("utf-8")

    with clients_lock:
        # Itera sobre cópia para poder remover durante iteração se necessário
        for conn in list(clients):
            if conn is exclude:
                continue
            try:
                conn.sendall(encoded)
            except OSError:
                # Cliente desconectou abruptamente; será limpo pela sua thread
                log.warning("Falha ao enviar para %s — conexão perdida.", clients.get(conn, "?"))


# ---------------------------------------------------------------------------
# Handler de cliente (executa em thread dedicada)
# ---------------------------------------------------------------------------

def handle_client(conn: socket.socket, addr: tuple) -> None:
    """
    Função executada em uma Thread dedicada para cada cliente conectado.

    Protocolo de handshake:
      1. Cliente envia JSON: {"type": "join", "username": "<nome>"}
      2. Servidor responde com histórico e notifica demais usuários.
      3. Loop de recepção de mensagens até desconexão.

    Args:
        conn:  Socket da conexão com o cliente.
        addr:  Endereço (IP, porta) do cliente.
    """
    username = None
    buffer = ""

    try:
        # --- Handshake: recebe identificação do cliente ---
        raw = conn.recv(1024).decode("utf-8")
        data = json.loads(raw.strip())

        if data.get("type") != "join":
            log.warning("Handshake inválido de %s. Encerrando.", addr)
            conn.close()
            return

        username = data["username"]
        log.info("'%s' conectou (%s:%s).", username, *addr)

        # Registra cliente na lista compartilhada
        with clients_lock:
            clients[conn] = username

        # Envia histórico apenas para o novo cliente
        history = load_history()
        conn.sendall((json.dumps({"type": "history", "messages": history}) + "\n").encode("utf-8"))

        # Notifica todos os outros sobre a entrada
        broadcast({"type": "system", "text": f"{username} entrou no chat."}, exclude=conn)

        # --- Loop principal: recebe mensagens em loop ---
        while True:
            chunk = conn.recv(4096).decode("utf-8")
            if not chunk:
                # Conexão encerrada pelo cliente
                break

            buffer += chunk

            # Processa todas as mensagens completas (delimitadas por '\n')
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.strip()
                if not line:
                    continue

                msg_data = json.loads(line)

                if msg_data.get("type") == "message":
                    text = msg_data.get("text", "")
                    log.info("[%s] %s", username, text)

                    # Persiste no banco de dados
                    save_message(username, text)

                    # Distribui para todos os clientes
                    broadcast({
                        "type": "message",
                        "username": username,
                        "text": text,
                    })

                elif msg_data.get("type") == "typing":
                    # Repassa indicador de digitação (sem persistir)
                    broadcast({
                        "type": "typing",
                        "username": username,
                        "typing": msg_data.get("typing", False),
                    }, exclude=conn)

    except (json.JSONDecodeError, KeyError) as e:
        log.error("Erro de protocolo de '%s': %s", username or addr, e)

    except ConnectionResetError:
        log.info("'%s' desconectou abruptamente.", username or addr)

    finally:
        # Limpeza: remove da lista e notifica demais usuários
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
    """
    Loop principal: aguarda novas conexões TCP e cria uma Thread para cada uma.

    Args:
        server_socket:  Socket do servidor já configurado e em listen.
    """
    log.info("Aguardando conexões na porta %s...", PORT)

    while True:
        try:
            conn, addr = server_socket.accept()
        except OSError:
            # Socket fechado (ex.: shutdown do servidor)
            break

        # Instancia thread dedicada para o novo cliente (requisito)
        thread = threading.Thread(
            target=handle_client,
            args=(conn, addr),
            daemon=True,
            name=f"client-{addr[0]}:{addr[1]}",
        )
        thread.start()
        log.info("Thread '%s' iniciada. Total de clientes: %d", thread.name, threading.active_count() - 1)


# ---------------------------------------------------------------------------
# Ponto de entrada
# ---------------------------------------------------------------------------

def main() -> None:
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    # Reutiliza porta imediatamente após restart (evita "Address already in use")
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind((HOST, PORT))
    server_socket.listen(10)

    log.info("Servidor %s iniciado na porta %s.", ROLE.upper(), PORT)

    # Inicia gerenciador de replicação em thread separada
    replication = ReplicationManager(
        role=ROLE,
        redis_url=REDIS_URL,
        server_socket=server_socket,
        port=PORT,
    )
    replication.start()

    try:
        accept_loop(server_socket)
    except KeyboardInterrupt:
        log.info("Encerrando servidor.")
    finally:
        server_socket.close()


if __name__ == "__main__":
    main()
