"""
db.py — Camada de acesso ao banco de dados PostgreSQL.

Responsabilidades:
  - Salvar mensagens enviadas no chat.
  - Carregar histórico de mensagens ao conectar novo cliente.
"""

import os
import logging

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

# URL de conexão (usa interna em produção, externa em debug)
_DEBUG = os.getenv("DEBUG", "false").lower() == "true"
_DATABASE_URL = os.getenv("EXTERNAL_DATABASE_URL" if _DEBUG else "INTERNAL_DATABASE_URL")

# Número máximo de mensagens retornadas no histórico
HISTORY_LIMIT = int(os.getenv("HISTORY_LIMIT", 50))


def _connect() -> psycopg2.extensions.connection:
    """Abre e retorna uma nova conexão com o banco de dados."""
    return psycopg2.connect(_DATABASE_URL)


def save_message(username: str, text: str) -> None:
    """
    Persiste uma mensagem no banco de dados.

    Args:
        username:  Nome do usuário que enviou a mensagem.
        text:      Conteúdo da mensagem.
    """
    sql = "INSERT INTO messages (username, message) VALUES (%s, %s)"
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (username, text))
    except psycopg2.Error as e:
        log.error("Erro ao salvar mensagem no banco: %s", e)


def load_history() -> list[dict]:
    """
    Retorna as últimas HISTORY_LIMIT mensagens do banco, em ordem cronológica.

    Returns:
        Lista de dicionários com chaves 'username', 'text' e 'sent_at'.
    """
    sql = """
        SELECT username, message, sent_at
        FROM messages
        ORDER BY sent_at DESC
        LIMIT %s
    """
    try:
        with _connect() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, (HISTORY_LIMIT,))
                rows = cur.fetchall()

        # Inverte para ordem cronológica (mais antigas primeiro)
        return [
            {
                "username": row["username"],
                "text": row["message"],
                "sent_at": row["sent_at"].isoformat(),
            }
            for row in reversed(rows)
        ]

    except psycopg2.Error as e:
        log.error("Erro ao carregar histórico do banco: %s", e)
        return []
