"""
client.py — Frontend do SockeText.

Responsabilidades:
  - Servir a interface web (HTML/JS) via Flask + Flask-SocketIO.
  - Manter uma conexão TCP separada com o backend para cada usuário conectado.
  - Instanciar uma Thread dedicada à recepção de mensagens TCP por usuário.
  - Retransmitir mensagens entre o browser (WebSocket/SocketIO) e o backend (TCP).
  - Fallback automático entre múltiplos servidores backend (tryConnect).
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
# Remove esquemas (http:// / https://) caso presentes antes de fazer o parse.
def _parse_servers(raw: str) -> list[tuple[str, int]]:
    result = []
    for s in raw.split(","):
        s = s.strip()
        if not s:
            continue
        # Remove esquema se existir
        if "://" in s:
            s = s.split("://", 1)[1]
        # Remove trailing slashes
        s = s.rstrip("/")
        # Separa host e porta
        if ":" in s:
            host, port_str = s.rsplit(":", 1)
            result.append((host, int(port_str)))
        else:
            # Sem porta explícita — usa 5000 como padrão
            result.append((s, 5000))
    return result

_raw_servers = os.getenv("BACKEND_SERVERS", "localhost:5000")
BACKEND_SERVERS: list[tuple[str, int]] = _parse_servers(_raw_servers)

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

_connections: dict[str, "BackendConnection"] = {}
_connections_lock = threading.Lock()


def get_connection(sid: str) -> "BackendConnection | None":
    with _connections_lock:
        return _connections.get(sid)


def register_connection(sid: str, conn: "BackendConnection") -> None:
    with _connections_lock:
        _connections[sid] = conn


def remove_connection(sid: str) -> None:
    with _connections_lock:
        conn = _connections.pop(sid, None)
    if conn:
        conn.disconnect()


# ---------------------------------------------------------------------------
# Gerenciador de conexão TCP com o backend
# ---------------------------------------------------------------------------

class BackendConnection:
    """
    Mantém a conexão TCP de UM usuário com o servidor backend.

    Implementa tryConnect: tenta cada servidor da lista em ordem,
    e reconecta automaticamente do início se todos falharem.
    """

    def __init__(self, sid: str) -> None:
        self.sid = sid
        self._sock: socket.socket | None = None
        self._send_lock = threading.Lock()
        self._connected = threading.Event()
        self._username: str = ""
        self._alive = True          # False quando o usuário desconecta do browser

    # ------------------------------------------------------------------
    # tryConnect — tenta cada servidor em ordem, reinicia se esgotar
    # ------------------------------------------------------------------

    def try_connect(self, username: str) -> None:
        """
        Tenta conectar a cada servidor backend em ordem.
        Se todos falharem, aguarda 2s e reinicia a tentativa do início.
        Roda em thread dedicada para não bloquear o handler SocketIO.

        Args:
            username: Nome do usuário (enviado no handshake TCP).
        """
        self._username = username

        threading.Thread(
            target=self._connect_loop,
            daemon=True,
            name=f"tcp-connect-{username}-{self.sid[:8]}",
        ).start()

    def _connect_loop(self) -> None:
        """Loop de tentativa de conexão — corre em thread separada."""
        while self._alive:
            for host, port in BACKEND_SERVERS:
                if not self._alive:
                    return

                try:
                    log.info("[%s] Tentando backend %s:%s...", self._username, host, port)

                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    sock.settimeout(3)
                    sock.connect((host, port))
                    sock.settimeout(None)

                    # Handshake
                    handshake = json.dumps({"type": "join", "username": self._username}) + "\n"
                    sock.sendall(handshake.encode("utf-8"))

                    self._sock = sock
                    self._connected.set()
                    log.info("[%s] Conectado ao backend %s:%s.", self._username, host, port)

                    # Bloqueia aqui até a conexão cair
                    self._receive_loop()

                    # Se chegou aqui, a conexão caiu — tenta o próximo servidor
                    self._connected.clear()

                    if not self._alive:
                        return

                    socketio.emit(
                        "system",
                        {"text": "Conexão perdida. Reconectando..."},
                        to=self.sid,
                    )

                except (OSError, ConnectionRefusedError) as e:
                    log.warning("[%s] Falha em %s:%s — %s", self._username, host, port, e)

            if self._alive:
                log.info("[%s] Todos os servidores falharam. Aguardando 2s...", self._username)
                time.sleep(2)

    # ------------------------------------------------------------------
    # Thread dedicada à recepção
    # ------------------------------------------------------------------

    def _receive_loop(self) -> None:
        """
        Loop de recepção TCP — bloqueia até a conexão cair.
        Retorna quando a conexão é encerrada (para que _connect_loop
        possa tentar o próximo servidor).
        """
        buffer = ""

        while self._alive:
            try:
                chunk = self._sock.recv(4096).decode("utf-8")
                if not chunk:
                    log.info("[%s] Servidor encerrou a conexão.", self._username)
                    return

                buffer += chunk

                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if not line:
                        continue

                    self._dispatch(json.loads(line))

            except OSError as e:
                log.error("[%s] Conexão TCP perdida: %s", self._username, e)
                return

            except json.JSONDecodeError as e:
                log.warning("[%s] Mensagem inválida do backend: %s", self._username, e)

    def _dispatch(self, payload: dict) -> None:
        """
        Retransmite uma mensagem do backend para o browser DESTE usuário.
        O `to=self.sid` garante entrega apenas ao browser correto.
        """
        msg_type = payload.get("type")

        if msg_type == "history":
            socketio.emit("history", {"messages": payload.get("messages", [])}, to=self.sid)

        elif msg_type == "message":
            socketio.emit("message", {
                "sender": payload.get("sender"),
                "text":   payload.get("text"),
                "time":   payload.get("time", ""),
            }, to=self.sid)

        elif msg_type == "system":
            socketio.emit("system", {"text": payload.get("text")}, to=self.sid)

        elif msg_type == "typing":
            socketio.emit("typing", {"sender": payload.get("sender")}, to=self.sid)

        elif msg_type == "stop_typing":
            socketio.emit("stop_typing", {"sender": payload.get("sender")}, to=self.sid)

    # ------------------------------------------------------------------
    # Envio de mensagens
    # ------------------------------------------------------------------

    def send(self, payload: dict) -> bool:
        """Envia um payload JSON ao backend via TCP."""
        if not self._connected.is_set() or self._sock is None:
            log.warning("[%s] Tentativa de envio sem conexão ativa.", self._username)
            return False

        message = (json.dumps(payload) + "\n").encode("utf-8")
        try:
            with self._send_lock:
                self._sock.sendall(message)
            return True
        except OSError as e:
            log.error("[%s] Erro ao enviar mensagem: %s", self._username, e)
            return False

    def disconnect(self) -> None:
        """Encerra a conexão TCP e para o loop de reconexão."""
        self._alive = False
        self._connected.clear()
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None


# ---------------------------------------------------------------------------
# Rotas HTTP
# ---------------------------------------------------------------------------

@app.route("/", methods=["GET"])
def index():
    if "username" in session:
        return redirect(url_for("chat"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        if username:
            session["username"] = username
            return redirect(url_for("chat"))
    return render_template("login.html")


@app.route("/chat")
def chat():
    if "username" not in session:
        return redirect(url_for("login"))
    return render_template("chat.html", username=session["username"])


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Eventos SocketIO (browser <-> frontend)
# ---------------------------------------------------------------------------

@socketio.on("connect")
def on_browser_connect():
    """Browser conectou — cria BackendConnection exclusiva e inicia tryConnect."""
    username = session.get("username")
    if not username:
        return False

    sid = request.sid

    if get_connection(sid) is not None:
        return

    conn = BackendConnection(sid)
    register_connection(sid, conn)
    conn.try_connect(username)


@socketio.on("send_message")
def on_send_message(data: dict):
    """Browser enviou mensagem — repassa ao backend via TCP."""
    text = data.get("text", "").strip()
    if not text:
        return

    conn = get_connection(request.sid)
    if conn:
        conn.send({"type": "message", "text": text})


@socketio.on("typing")
def on_typing():
    """Browser sinalizou que está digitando."""
    conn = get_connection(request.sid)
    if conn:
        conn.send({"type": "typing"})


@socketio.on("stop_typing")
def on_stop_typing():
    """Browser sinalizou que parou de digitar."""
    conn = get_connection(request.sid)
    if conn:
        conn.send({"type": "stop_typing"})


@socketio.on("disconnect")
def on_browser_disconnect():
    """Browser desconectou — encerra a conexão TCP deste usuário."""
    username = session.get("username", request.sid[:8])
    log.info("[%s] Browser desconectou.", username)
    remove_connection(request.sid)


# ---------------------------------------------------------------------------
# Ponto de entrada
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    log.info("Frontend iniciado na porta %s.", PORT)
    socketio.run(app, host="0.0.0.0", port=PORT, debug=False)
