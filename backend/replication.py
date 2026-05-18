"""
replication.py — Mecanismo de replicação primário/réplica via Redis.

Funcionamento:
  - O servidor PRIMÁRIO publica um heartbeat no Redis a cada HEARTBEAT_INTERVAL segundos.
  - O servidor RÉPLICA monitora o heartbeat. Se não receber dentro de FAILOVER_TIMEOUT,
    assume o papel de primário: abre um novo socket na porta do primário e começa a
    aceitar conexões.

Variáveis de ambiente relevantes:
  ROLE              — "primary" ou "replica"
  PRIMARY_PORT      — porta do servidor primário (padrão: 5000)
  REPLICA_PORT      — porta do servidor réplica  (padrão: 5001)
  REDIS_URL         — URL de conexão Redis
"""

import os
import socket
import threading
import time
import logging

import redis

log = logging.getLogger(__name__)

# Chave Redis usada para o heartbeat
HEARTBEAT_KEY = "socketext:heartbeat"

# Intervalo (segundos) entre publicações de heartbeat
HEARTBEAT_INTERVAL = float(os.getenv("HEARTBEAT_INTERVAL", 2))

# Tempo máximo (segundos) sem heartbeat antes do failover
FAILOVER_TIMEOUT = float(os.getenv("FAILOVER_TIMEOUT", 6))


class ReplicationManager:
    """
    Gerencia a lógica de replicação para servidores primário e réplica.

    O primário publica heartbeats periódicos no Redis.
    A réplica monitora e executa failover se necessário.
    """

    def __init__(
        self,
        role: str,
        redis_url: str,
        server_socket: socket.socket,
        port: int,
    ) -> None:
        """
        Args:
            role:           "primary" ou "replica".
            redis_url:      URL de conexão com o Redis.
            server_socket:  Socket do servidor (usado pela réplica ao assumir).
            port:           Porta em que este servidor escuta.
        """
        self.role = role
        self.redis_url = redis_url
        self.server_socket = server_socket
        self.port = port
        self._redis = redis.from_url(redis_url, decode_responses=True)

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Inicia a thread de replicação adequada ao papel deste servidor."""
        if self.role == "primary":
            t = threading.Thread(
                target=self._heartbeat_loop,
                daemon=True,
                name="replication-heartbeat",
            )
        else:
            t = threading.Thread(
                target=self._monitor_loop,
                daemon=True,
                name="replication-monitor",
            )

        t.start()
        log.info("ReplicationManager iniciado como %s.", self.role.upper())

    # ------------------------------------------------------------------
    # Primário: publica heartbeat
    # ------------------------------------------------------------------

    def _heartbeat_loop(self) -> None:
        """
        Loop do primário: publica timestamp atual no Redis a cada
        HEARTBEAT_INTERVAL segundos, sinalizando que está vivo.
        """
        while True:
            try:
                self._redis.set(HEARTBEAT_KEY, time.time(), ex=int(FAILOVER_TIMEOUT * 2))
                log.debug("Heartbeat publicado.")
            except redis.RedisError as e:
                log.error("Erro ao publicar heartbeat: %s", e)

            time.sleep(HEARTBEAT_INTERVAL)

    # ------------------------------------------------------------------
    # Réplica: monitora heartbeat e executa failover se necessário
    # ------------------------------------------------------------------

    def _monitor_loop(self) -> None:
        """
        Loop da réplica: verifica periodicamente se o primário está vivo.
        Se o heartbeat desaparecer por FAILOVER_TIMEOUT segundos, executa
        o failover e passa a aceitar conexões.
        """
        log.info("Monitorando primário (timeout: %ss)...", FAILOVER_TIMEOUT)

        while True:
            time.sleep(HEARTBEAT_INTERVAL)

            try:
                last = self._redis.get(HEARTBEAT_KEY)
            except redis.RedisError as e:
                log.error("Erro ao ler heartbeat do Redis: %s", e)
                continue

            if last is None:
                log.warning("Heartbeat ausente! Aguardando confirmação...")
                time.sleep(FAILOVER_TIMEOUT)

                # Verifica novamente após espera
                try:
                    last = self._redis.get(HEARTBEAT_KEY)
                except redis.RedisError:
                    last = None

                if last is None:
                    log.critical("Primário não respondeu. Executando FAILOVER.")
                    self._do_failover()
                    return  # Encerra o monitor; servidor agora é primário

            else:
                elapsed = time.time() - float(last)
                log.debug("Heartbeat recebido (%.1fs atrás).", elapsed)

    def _do_failover(self) -> None:
        """
        Executa o failover: a réplica assume o papel de primário.

        Passos:
          1. Publica novo heartbeat para sinalizar que está vivo.
          2. Reabre o socket na porta do primário para aceitar suas conexões.
          3. Inicia o loop de heartbeat como novo primário.
        """
        primary_port = int(os.getenv("PRIMARY_PORT", 5000))

        log.info("Assumindo papel de PRIMÁRIO na porta %s.", primary_port)

        # Sinaliza presença imediatamente
        try:
            self._redis.set(HEARTBEAT_KEY, time.time(), ex=int(FAILOVER_TIMEOUT * 2))
        except redis.RedisError:
            pass

        # Tenta abrir socket na porta do primário
        try:
            new_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            new_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            new_socket.bind(("0.0.0.0", primary_port))
            new_socket.listen(10)

            # Importa accept_loop aqui para evitar importação circular
            from server import accept_loop
            self.role = "primary"

            # Inicia loop de heartbeat como novo primário
            threading.Thread(
                target=self._heartbeat_loop,
                daemon=True,
                name="replication-heartbeat-failover",
            ).start()

            log.info("Failover concluído. Aceitando conexões na porta %s.", primary_port)
            accept_loop(new_socket)

        except OSError as e:
            log.error("Falha ao abrir socket de failover: %s", e)
