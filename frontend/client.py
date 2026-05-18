"""
client.py — Frontend do SockeText.

Responsabilidades:
  - Servir a interface web (HTML/JS) via Flask + Flask-SocketIO.
  - Manter uma conexão TCP separada com o backend para cada usuário conectado.
  - Instanciar uma Thread dedicada à recepção de mensagens TCP por usuário (requisito).
  - Retransmitir mensagens entre o browser (WebSocket) e o backend (TCP).
  - Implementar fallback automático entre múltiplos servidores backend.
"""

import json
import os
import socket
import threading
import logging
import time

from flask import Flask, render_template, session, redirect, url_for, request
from flask_socketio import SocketIO, emit
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------

load_dotenv()

SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me")

# Lista de endereços backend: "host:porta,host:porta"
_raw_servers = os.getenv("BACKEND_SERVERS", "localhost:5000")
BACKEND_SERVERS: list[tuple[str, int]] = [
    (s.split(":")[0], int(s.split(":")[1]))
    for s in _raw_servers.split(",")
    if s.strip()
]

PORT = int(os.getenv("PORT", 8000))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# App Flask
# ---------------------------------------------------------------------------

app = Flask(__name__)
app.secret_key = SECRET_KEY

# async_mode="threading" garante compatibilidade com threads manuais
socketio = SocketIO(app, async_mode="threading", cors_allowed_origins="*")

# ---------------------------------------------------------------------------
# Registro de conexões: uma por usuário (sid do Socket.IO)
# ---------------------------------------------------------------------------

# Dicionário {sid: BackendConnection} — cada browser tem sua própria conexão TCP
_connections: dict[str, "BackendConnection"] = {}
_connections_lock = threading.Lock()


def get_connection(sid: str) -> "BackendConnection | None":
    """Retorna a conexão TCP associada a um sid, ou None se não existir."""
    with _connections_lock:
        return _connections.get(sid)


def register_connection(sid: str, conn: "BackendConnection") -> None:
    """Registra uma nova conexão TCP para o sid informado."""
    with _connections_lock:
        _connections[sid] = conn


def remove_connection(sid: str) -> None:
    """Remove e encerra a conexão TCP do sid informado."""
    with _connections_lock:
        conn = _connections.pop(sid, None)
    if conn:
        conn.disconnect()
        log.info("Conexão TCP do sid '%s' encerrada.", sid)


# ---------------------------------------------------------------------------
# Gerenciador de conexão TCP com o backend (uma instância por usuário)
# ---------------------------------------------------------------------------

class BackendConnection:
    """
    Mantém a conexão TCP de UM usuário com o servidor backend.

    Cada browser conectado gera uma instância independente desta classe,
    garantindo que usuários distintos não compartilhem estado de conexão.
    """

    def __init__(self, sid: str) -> None:
        """
        Args:
            sid:  ID da sessão Socket.IO do browser deste usuário.
                  Usado para emitir eventos de volta ao browser correto.
        """
        self.sid = sid
        self._sock: socket.socket | None = None
        self._send_lock = threading.Lock()      # Protege envios simultâneos
        self._connected = threading.Event()     # Sinaliza quando a conexão está ativa
        self._username: str = ""

    # ------------------------------------------------------------------
    # Conexão e reconexão
    # ------------------------------------------------------------------

    def connect(self, username: str) -> bool:
        """
        Tenta conectar a um dos servidores backend disponíveis em ordem.

        Envia o handshake de identificação ao conectar com sucesso e
        inicia a thread dedicada à recepção de mensagens.

        Args:
            username:  Nome do usuário (enviado no handshake).

        Returns:
            True se conectou com sucesso, False caso contrário.
        """
        self._username = username

        for host, port in BACKEND_SERVERS:
            try:
                log.info("[%s] Tentando backend %s:%s...", username, host, port)
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(5)
                sock.connect((host, port))
                sock.settimeout(None)

                # Handshake: identifica o usuário para o servidor
                handshake = json.dumps({"type": "join", "username": username}) + "\n"
                sock.sendall(handshake.encode("utf-8"))

                self._sock = sock
                self._connected.set()
                log.info("[%s] Conectado ao backend %s:%s.", username, host, port)

                # Inicia thread dedicada à recepção (requisito)
                self._start_receive_thread()
                return True

            except (OSError, ConnectionRefusedError) as e:
                log.warning("[%s] Falha em %s:%s — %s", username, host, port, e)

        log.error("[%s] Nenhum servidor backend disponível.", username)
        return False

    def reconnect(self) -> None:
        """
        Loop de reconexão com backoff exponencial.
        Notifica o browser correto sobre o status da reconexão.
        """
        self._connected.clear()
        delay = 2

        while True:
            log.info("[%s] Reconectando em %ss...", self._username, delay)
            time.sleep(delay)

            if self.connect(self._username):
                # Emite apenas para o browser deste usuário (via sid)
                socketio.emit("system", {"text": "Reconectado ao servidor."}, to=self.sid)
                return

            delay = min(delay * 2, 30)

    # ------------------------------------------------------------------
    # Thread dedicada à recepção (requisito explícito)
    # ------------------------------------------------------------------

    def _start_receive_thread(self) -> None:
        """
        Instancia e inicia a Thread dedicada à recepção de mensagens TCP.

        Esta thread fica bloqueada em recv() aguardando dados do backend,
        desacoplando completamente a recepção do envio.
        """
        t = threading.Thread(
            target=self._receive_loop,
            daemon=True,
            name=f"tcp-recv-{self._username}-{self.sid[:8]}",
        )
        t.start()
        log.info("Thread de recepção '%s' iniciada.", t.name)

    def _receive_loop(self) -> None:
        """
        Loop de recepção TCP — executa na thread dedicada deste usuário.

        Lê mensagens do backend (delimitadas por newline), faz parse do JSON
        e retransmite SOMENTE para o browser deste usuário via socketio.emit(..., to=sid).
        """
        buffer = ""

        while True:
            try:
                chunk = self._sock.recv(4096).decode("utf-8")
                if not chunk:
                    raise ConnectionResetError("Servidor encerrou a conexão.")

                buffer += chunk

                # Processa todas as mensagens completas no buffer
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if not line:
                        continue

                    self._dispatch(json.loads(line))

            except (OSError, ConnectionResetError) as e:
                log.error("[%s] Conexão TCP perdida: %s", self._username, e)
                self._connected.clear()
                socketio.emit(
                    "system",
                    {"text": "Conexão perdida. Reconectando..."},
                    to=self.sid,
                )
                self.reconnect()
                return  # Reconexão inicia nova thread

            except json.JSONDecodeError as e:
                log.warning("[%s] Mensagem inválida do backend: %s", self._username, e)

    def _dispatch(self, payload: dict) -> None:
        """
        Retransmite uma mensagem do backend para o browser DESTE usuário.

        O `to=self.sid` garante que a mensagem vai apenas para o browser
        correto, não para todos os conectados.

        Args:
            payload:  Dicionário com os dados recebidos do backend.
        """
        msg_type = payload.get("type")

        if msg_type == "history":
            socketio.emit("history", {"messages": payload.get("messages", [])}, to=self.sid)

        elif msg_type == "message":
            socketio.emit("message", {
                "username": payload.get("username"),
                "text":     payload.get("text"),
                "sent_at":  payload.get("sent_at", ""),
            }, to=self.sid)

        elif msg_type == "system":
            socketio.emit("system", {"text": payload.get("text")}, to=self.sid)

        elif msg_type == "typing":
            socketio.emit("typing", {
                "username": payload.get("username"),
                "typing":   payload.get("typing", False),
            }, to=self.sid)

    # ------------------------------------------------------------------
    # Envio de mensagens
    # ------------------------------------------------------------------

    def send(self, payload: dict) -> bool:
        """
        Envia um payload JSON ao backend via TCP.

        Args:
            payload:  Dicionário a serializar e enviar.

        Returns:
            True se enviou com sucesso, False caso contrário.
        """
        if not self._connected.is_set() or self._sock is None:
            log.warning("[%s] Tentativa de envio sem conexão ativa.", self._username)
            return False

        message = json.dumps(payload) + "\n"
        try:
            with self._send_lock:
                self._sock.sendall(message.encode("utf-8"))
            return True
        except OSError as e:
            log.error("[%s] Erro ao enviar mensagem: %s", self._username, e)
            return False

    def disconnect(self) -> None:
        """Encerra a conexão TCP com o backend."""
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
            self._connected.clear()


# ---------------------------------------------------------------------------
# Rotas HTTP
# ---------------------------------------------------------------------------

@app.route("/", methods=["GET"])
def index():
    """Redireciona para o chat se logado, ou para login."""
    if "username" in session:
        return redirect(url_for("chat"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    """Exibe e processa o formulário de login."""
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        if username:
            session["username"] = username
            return redirect(url_for("chat"))

    return render_template("login.html")


@app.route("/chat")
def chat():
    """Exibe a interface principal do chat."""
    if "username" not in session:
        return redirect(url_for("login"))
    return render_template("chat.html", username=session["username"])


@app.route("/logout")
def logout():
    """Encerra a sessão."""
    session.clear()
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Eventos SocketIO (browser <-> frontend)
# ---------------------------------------------------------------------------

@socketio.on("connect")
def on_browser_connect():
    """
    Browser conectou via WebSocket.

    Cria uma nova BackendConnection exclusiva para este usuário (identificada
    pelo request.sid) e inicia a conexão TCP com o backend.
    """
    username = session.get("username")
    if not username:
        return False  # Rejeita conexão sem sessão

    sid = request.sid

    # Garante que não há conexão duplicada para o mesmo sid
    if get_connection(sid) is not None:
        log.info("[%s] sid '%s' já tem conexão ativa.", username, sid[:8])
        return

    # Cria conexão TCP individual para este usuário
    conn = BackendConnection(sid)
    register_connection(sid, conn)

    ok = conn.connect(username)
    if not ok:
        emit("system", {"text": "Erro: não foi possível conectar ao servidor."})


@socketio.on("send_message")
def on_send_message(data: dict):
    """
    Browser enviou uma mensagem — repassa ao backend via TCP.

    Args:
        data:  Dicionário com chave 'text'.
    """
    text = data.get("text", "").strip()
    if not text:
        return

    conn = get_connection(request.sid)
    if conn:
        conn.send({"type": "message", "text": text})


@socketio.on("typing")
def on_typing(data: dict):
    """
    Browser sinalizou estado de digitação — repassa ao backend.

    Args:
        data:  Dicionário com chave 'typing' (bool).
    """
    conn = get_connection(request.sid)
    if conn:
        conn.send({"type": "typing", "typing": data.get("typing", False)})


@socketio.on("disconnect")
def on_browser_disconnect():
    """
    Browser desconectou — remove e encerra a conexão TCP deste usuário.
    """
    sid = request.sid
    username = session.get("username", sid[:8])
    log.info("[%s] Browser desconectou. Encerrando conexão TCP.", username)
    remove_connection(sid)


# ---------------------------------------------------------------------------
# Ponto de entrada
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    log.info("Frontend iniciado na porta %s.", PORT)
    socketio.run(app, host="0.0.0.0", port=PORT, debug=False)
