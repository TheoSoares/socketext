"""
SockeText — Backend
Servidor WebSocket puro usando a biblioteca `websockets` + threading.

Arquitetura de threads:
  - Thread HTTP        → Flask serve /history e /health
  - Thread WS-Server   → websockets.sync.server aceita conexões
  - Thread por cliente → handle_client() — uma thread dedicada por conexão
  - Thread Replicação  → primário mantém WS com réplica (replication_connector)
  - Thread Monitor     → réplica detecta queda do primário (primary_monitor)

Tolerância a falhas:
  - Primário envia {"type":"__primary__"} ao conectar na réplica.
  - Réplica detecta queda pelo fechamento da conexão e assume como nó ativo.
  - Frontend tenta servidores em sequência; reconecta automaticamente.
"""

import os
import json
import threading
import time
import logging
from datetime import datetime

from flask import Flask, jsonify
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text
from dotenv import load_dotenv

import websockets.sync.server as ws_sync
import websockets.sync.client as ws_client_sync

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(threadName)s — %(message)s",
)
log = logging.getLogger(__name__)

# ── Configuração ──────────────────────────────────────────────────────────────

IS_REPLICA      = os.getenv("IS_REPLICA", "false").lower() == "true"
WS_HOST         = os.getenv("WS_HOST", "0.0.0.0")
WS_PORT         = int(os.getenv("WS_PORT", 9000))
HTTP_PORT       = int(os.getenv("HTTP_PORT", 5000))
REPLICA_WS_HOST = os.getenv("REPLICA_WS_HOST", "127.0.0.1")
REPLICA_WS_PORT = int(os.getenv("REPLICA_WS_PORT", 9001))
DEBUG_MODE      = os.getenv("DEBUG", "false").lower() == "true"

# ── Flask / DB ────────────────────────────────────────────────────────────────

flask_app = Flask(__name__)
flask_app.secret_key = os.getenv("SECRET_KEY", "dev-secret")
flask_app.config["SQLALCHEMY_DATABASE_URI"] = (
    os.getenv("EXTERNAL_DATABASE_URL") if DEBUG_MODE
    else os.getenv("INTERNAL_DATABASE_URL")
)
flask_app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(flask_app)

@flask_app.route("/history")
def route_history():
    with flask_app.app_context():
        rows = db.session.execute(
            text("SELECT username, sent_at, message FROM messages ORDER BY sent_at")
        ).fetchall()
    return jsonify([
        {"sender": r[0], "time": r[1].strftime("%H:%M"), "text": r[2]}
        for r in rows
    ])

@flask_app.route("/health")
def route_health():
    return jsonify({"ok": True, "replica": IS_REPLICA, "wsPort": WS_PORT})

# ── Estado global compartilhado entre threads ─────────────────────────────────

clients: dict = {}       # sid -> websocket connection
clients_lock  = threading.Lock()
client_counter = 0
counter_lock   = threading.Lock()

replica_ws   = None      # conexão com a réplica (usado pelo primário)
replica_lock = threading.Lock()

# ── Helpers ───────────────────────────────────────────────────────────────────

def _send(ws, payload: dict) -> bool:
    try:
        ws.send(json.dumps(payload, ensure_ascii=False))
        return True
    except Exception:
        return False

def broadcast(payload: dict, exclude: str = None):
    with clients_lock:
        snapshot = list(clients.items())
    dead = []
    for sid, ws in snapshot:
        if sid == exclude:
            continue
        if not _send(ws, payload):
            dead.append(sid)
    for sid in dead:
        _remove_client(sid)

def _remove_client(sid: str):
    with clients_lock:
        ws = clients.pop(sid, None)
    if ws:
        log.info("Removido: %s", sid)
        try:
            ws.close()
        except Exception:
            pass

def forward_to_replica(payload: dict):
    global replica_ws
    with replica_lock:
        rws = replica_ws
    if rws is None:
        return
    try:
        rws.send(json.dumps({"__replica__": True, **payload}, ensure_ascii=False))
    except Exception as e:
        log.warning("Falha ao replicar: %s", e)
        with replica_lock:
            replica_ws = None

def load_history():
    with flask_app.app_context():
        rows = db.session.execute(
            text("SELECT username, sent_at, message FROM messages ORDER BY sent_at")
        ).fetchall()
        return [
            {"sender": r[0], "time": r[1].strftime("%H:%M"), "text": r[2]}
            for r in rows
        ]

def persist_message(sender: str, text_body: str):
    with flask_app.app_context():
        db.session.execute(
            text("INSERT INTO messages (username, message) VALUES (:u, :m)"),
            {"u": sender, "m": text_body},
        )
        db.session.commit()

# ── Processador de frames ─────────────────────────────────────────────────────

def process_frame(msg: dict, sid: str):
    """Processa um frame recebido de um cliente."""
    mtype = msg.get("type")

    if mtype in ("join", "__heartbeat__"):
        return

    if mtype in ("typing", "stop_typing"):
        payload = {"type": mtype, "sender": msg.get("sender", "?")}
        broadcast(payload, exclude=sid)
        if not IS_REPLICA:
            forward_to_replica(payload)
        return

    if mtype == "message":
        text_body = msg.get("text", "").strip()
        sender    = msg.get("sender", "Anônimo")
        if not text_body:
            return
        now = datetime.now()
        payload = {
            "type":   "message",
            "sender": sender,
            "text":   text_body,
            "time":   now.strftime("%H:%M"),
        }
        if not IS_REPLICA:
            try:
                persist_message(sender, text_body)
            except Exception as e:
                log.error("Erro ao persistir: %s", e)
            forward_to_replica(payload)
        broadcast(payload, exclude=sid)

# ── Thread por cliente ────────────────────────────────────────────────────────

def handle_client(ws, sid: str):
    """
    Thread dedicada a uma conexão WebSocket.
    Envia o histórico imediatamente e entra no loop de recepção.
    """
    with clients_lock:
        clients[sid] = ws

    log.info("Conectado: %s %s", sid, ws.remote_address)

    # Envia histórico
    try:
        _send(ws, {"type": "history_load", "data": load_history()})
    except Exception as e:
        log.error("Erro ao enviar histórico para %s: %s", sid, e)

    # Loop de recepção (esta thread fica bloqueada aqui)
    try:
        for raw in ws:
            try:
                process_frame(json.loads(raw), sid)
            except json.JSONDecodeError:
                continue
    except Exception as e:
        log.debug("Conexão encerrada (%s): %s", sid, e)
    finally:
        _remove_client(sid)
        log.info("Desconectado: %s", sid)

# ── Handler do servidor WebSocket ────────────────────────────────────────────

def ws_handler(ws):
    """
    Ponto de entrada do websockets.sync.server para cada nova conexão.
    Detecta se é o primário se registrando (na réplica) ou um cliente comum.
    """
    global client_counter, replica_ws

    # Lê o primeiro frame para identificar o tipo de conexão
    try:
        first_raw = ws.recv(timeout=3)
        first = json.loads(first_raw)
    except Exception:
        first = {}

    # Primário registrando-se na réplica
    if first.get("type") == "__primary__":
        if IS_REPLICA:
            log.info("Primário conectado — iniciando monitor")
            primary_monitor(ws)   # bloqueia até o primário cair
        return

    # Cliente comum
    with counter_lock:
        client_counter += 1
        sid = f"client-{client_counter}"

    # Cria thread dedicada e aguarda o primeiro frame
    def _run():
        with clients_lock:
            clients[sid] = ws

        log.info("Conectado: %s %s", sid, ws.remote_address)

        # Envia histórico
        try:
            _send(ws, {"type": "history_load", "data": load_history()})
        except Exception as e:
            log.error("Erro ao enviar histórico para %s: %s", sid, e)

        # Processa o primeiro frame
        if first:
            try:
                process_frame(first, sid)
            except Exception:
                pass

        # Loop de recepção
        try:
            for raw in ws:
                try:
                    process_frame(json.loads(raw), sid)
                except json.JSONDecodeError:
                    continue
        except Exception as e:
            log.debug("Conexão encerrada (%s): %s", sid, e)
        finally:
            _remove_client(sid)
            log.info("Desconectado: %s", sid)

    t = threading.Thread(target=_run, name=f"Client-{sid}", daemon=True)
    t.start()
    t.join()   # ws_handler deve bloquear enquanto o cliente está conectado

# ── Thread de replicação (primário) ──────────────────────────────────────────

def replication_connector():
    """Primário: mantém conexão WebSocket com a réplica e envia heartbeats."""
    global replica_ws
    url = f"ws://{REPLICA_WS_HOST}:{REPLICA_WS_PORT}"
    while True:
        try:
            log.info("Conectando à réplica: %s…", url)
            with ws_client_sync.connect(url) as rws:
                rws.send(json.dumps({"type": "__primary__"}))
                with replica_lock:
                    replica_ws = rws
                log.info("✓ Réplica conectada")
                while True:
                    time.sleep(5)
                    try:
                        rws.send(json.dumps({"type": "__heartbeat__"}))
                    except Exception:
                        break
        except Exception as e:
            log.warning("Réplica indisponível: %s — tentando em 5 s…", e)
        with replica_lock:
            replica_ws = None
        time.sleep(5)

# ── Monitor do primário (réplica) ─────────────────────────────────────────────

def primary_monitor(primary_ws):
    """Réplica: lê frames do primário e distribui para clientes locais."""
    log.info("Monitorando primário…")
    try:
        for raw in primary_ws:
            try:
                frame = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if frame.get("type") == "__heartbeat__":
                continue
            if frame.get("__replica__"):
                inner = {k: v for k, v in frame.items() if k != "__replica__"}
                broadcast(inner)
    except Exception as e:
        log.debug("Monitor encerrado: %s", e)

    log.warning(
        "⚠ Primário caiu! Réplica %s:%s agora é o nó ativo.",
        WS_HOST, WS_PORT,
    )

# ── Entry point ───────────────────────────────────────────────────────────────

def run_http():
    log.info("HTTP Flask ouvindo na porta %s", HTTP_PORT)
    flask_app.run(host="0.0.0.0", port=HTTP_PORT, use_reloader=False)

def run_ws():
    role = "réplica" if IS_REPLICA else "primário"
    log.info("WebSocket (%s) ouvindo em %s:%s", role, WS_HOST, WS_PORT)
    with ws_sync.serve(ws_handler, WS_HOST, WS_PORT) as server:
        server.serve_forever()

if __name__ == "__main__":
    threading.Thread(target=run_http, name="HTTP", daemon=True).start()

    if not IS_REPLICA:
        threading.Thread(
            target=replication_connector, name="ReplicaConn", daemon=True
        ).start()

    try:
        run_ws()
    except KeyboardInterrupt:
        log.info("Servidor encerrado.")
