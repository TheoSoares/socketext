# ★ SockeText

Chat em tempo real com suporte a múltiplos usuários, histórico persistente e tolerância a falhas.

## Arquitetura

```
[Browser]
    │  WebSocket (Socket.IO)
    ▼
[Frontend — Flask + SocketIO]     porta 8000
    │  TCP socket (cliente manual)
    │  Thread de recepção dedicada  ← requisito
    ▼
[Backend — TCP Server]
    ├── Primário  (porta 5000)  ← Thread por conexão de cliente
    └── Réplica   (porta 5001)  ← assume se o primário cair
            │
        PostgreSQL  (histórico de mensagens)
        Redis       (heartbeat de replicação)
```

### Por que essa arquitetura atende os requisitos

| Requisito | Implementação |
|---|---|
| Thread por conexão no servidor | `backend/server.py` — `threading.Thread` instanciada em `accept_loop()` para cada frontend conectado |
| Thread de recepção no cliente | `frontend/client.py` — `BackendConnection._start_receive_thread()` cria thread dedicada que fica bloqueada em `recv()` |
| Interface web | Flask serve HTML/CSS/JS; browser usa Socket.IO |
| Tolerância a falhas | `backend/replication.py` — primário publica heartbeat no Redis; réplica monitora e executa failover |

---

## Estrutura de arquivos

```
socketext/
├── backend/
│   ├── server.py          # Servidor TCP: aceita conexões, threads por cliente, broadcast
│   ├── db.py              # Camada PostgreSQL: salvar e carregar histórico
│   ├── replication.py     # Replicação: heartbeat Redis, failover automático
│   ├── requirements.txt
│   └── .env.example
├── frontend/
│   ├── client.py          # Flask HTTP + cliente TCP com thread de recepção
│   ├── requirements.txt
│   ├── .env.example
│   ├── templates/
│   │   ├── login.html
│   │   └── chat.html
│   └── static/
│       ├── script.js      # Socket.IO client: envio, recepção, indicador de digitação
│       └── style.css
├── schema.sql             # DDL do banco de dados
└── README.md
```

---

## Instalação

### 1. Clone o repositório

```bash
git clone https://github.com/seu-usuario/socketext.git
cd socketext
```

### 2. Crie a tabela no banco de dados

```bash
psql -U usuario -d socketext -f schema.sql
```

### 3. Configure os arquivos `.env`

**Backend:**
```bash
cd backend
cp .env.example .env
# Edite .env com suas credenciais
```

**Frontend:**
```bash
cd frontend
cp .env.example .env
# Edite .env com suas credenciais
```

### 4. Instale as dependências

```bash
cd backend && pip install -r requirements.txt
cd ../frontend && pip install -r requirements.txt
```

---

## Executando

### Servidor primário

```bash
cd backend
ROLE=primary PORT=5000 python server.py
```

### Servidor réplica

```bash
cd backend
ROLE=replica PORT=5001 python server.py
```

### Frontend

```bash
cd frontend
python client.py
```

Acesse **http://localhost:8000**, digite seu nome e comece a conversar.

---

## Como funciona a replicação

1. O servidor **primário** publica um timestamp no Redis a cada `HEARTBEAT_INTERVAL` segundos (padrão: 2s).
2. O servidor **réplica** monitora essa chave. Se ela não for atualizada por `FAILOVER_TIMEOUT` segundos (padrão: 6s), executa o failover.
3. No failover, a réplica abre um novo socket TCP na porta do primário e passa a aceitar conexões, tornando-se o novo primário.
4. O frontend tenta os servidores em ordem (`BACKEND_SERVERS`); se o primeiro cair, reconecta automaticamente ao próximo.

---

## Variáveis de ambiente

### `backend/.env`

| Variável | Descrição | Padrão |
|---|---|---|
| `ROLE` | `primary` ou `replica` | `primary` |
| `PORT` | Porta TCP deste servidor | `5000` |
| `PRIMARY_PORT` | Porta do primário (usada no failover) | `5000` |
| `INTERNAL_DATABASE_URL` | PostgreSQL em produção | — |
| `EXTERNAL_DATABASE_URL` | PostgreSQL em debug | — |
| `DEBUG` | `true` usa URL externa | `false` |
| `REDIS_URL` | URL de conexão Redis | `redis://localhost:6379` |
| `HISTORY_LIMIT` | Mensagens no histórico inicial | `50` |
| `HEARTBEAT_INTERVAL` | Segundos entre heartbeats | `2` |
| `FAILOVER_TIMEOUT` | Timeout para failover | `6` |

### `frontend/.env`

| Variável | Descrição | Padrão |
|---|---|---|
| `SECRET_KEY` | Chave secreta Flask | — |
| `BACKEND_SERVERS` | `host:porta` separados por vírgula | `localhost:5000` |
| `PORT` | Porta HTTP do frontend | `8000` |

---

## Protocolo TCP (backend ↔ frontend)

Todas as mensagens são JSON delimitadas por `\n`.

### Handshake (frontend → backend)
```json
{"type": "join", "username": "Alice"}
```

### Histórico (backend → frontend, logo após handshake)
```json
{"type": "history", "messages": [{"username": "...", "text": "...", "sent_at": "..."}]}
```

### Mensagem (bidirecional)
```json
{"type": "message", "username": "Alice", "text": "Olá!"}
```

### Digitando (frontend → backend → outros frontends)
```json
{"type": "typing", "username": "Alice", "typing": true}
```

### Sistema (backend → frontend)
```json
{"type": "system", "text": "Alice entrou no chat."}
```
