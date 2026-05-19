# ★ SockeText

Chat em tempo real com suporte a múltiplos servidores WebSocket, histórico persistente, indicador de digitação e tolerância a falhas por replicação.

Construído com **WebSockets puros + threading** no backend, **Flask** no frontend e **PostgreSQL** como banco de dados. **Sem Flask-SocketIO.**

---

## Arquitetura

```
frontend/               → Cliente Flask (porta definida por PORT)
│  client.py            → Rotas HTTP (login, chat)
│  requirements.txt
│  templates/           → HTML (login.html, chat.html)
│  static/
│     script.js         → Web Worker para recepção + fila de envio
│     style.css
│
backend/                → Servidor WebSocket (porta definida por WS_PORT)
│  server.py            → Threads: HTTP, WS-Acceptor, por-cliente, replicação
│  requirements.txt
```

### Modelo de threads

| Thread | Responsável |
|---|---|
| `HTTP` | Flask serve `/history` e `/health` |
| `WS-Server` | `websockets.sync.server` — aceita conexões |
| `Client-N` | Uma thread por cliente — loop de recepção e envio |
| `ReplicaConn` | Primário mantém conexão com a réplica e envia heartbeats |
| `PrimaryMonitor` | Réplica detecta queda do primário e distribui mensagens replicadas |
| `RecvWorker` (browser) | Web Worker dedicado à recepção de mensagens no cliente |

### Protocolo

Conexão WebSocket nativa (não Socket.IO). Cada mensagem é um JSON.

Tipos de frame:

| `type` | Direção | Descrição |
|---|---|---|
| `join` | cliente → servidor | identificação ao conectar |
| `history_load` | servidor → cliente | histórico completo |
| `message` | bidirecional | mensagem de chat |
| `typing` / `stop_typing` | cliente → servidor → outros | indicador de digitação |
| `__primary__` | primário → réplica | registro da conexão de replicação |
| `__heartbeat__` | primário → réplica | keep-alive a cada 5 s |
| `__replica__` | primário → réplica | envelope de mensagem replicada |

---

## Funcionalidades

- Mensagens em tempo real via WebSocket puro
- Uma **thread dedicada por conexão** no servidor
- **Web Worker** dedicado à recepção no navegador
- Histórico de mensagens persistido em PostgreSQL
- Indicador de "digitando…" para outros participantes
- Fila de envio com reordenamento pós-reconexão
- **Replicação automática**: primário replica mensagens para o secundário
- **Fallback automático**: frontend tenta servidores em sequência
- Interface responsiva em português

---

## Requisitos

- Python 3.10+
- PostgreSQL

---

## Instalação

### 1. Clone o repositório

```bash
git clone https://github.com/seu-usuario/socketext.git
cd socketext
```

### 2. Instale as dependências

```bash
cd backend  && pip install -r requirements.txt
cd ../frontend && pip install -r requirements.txt
```

### 3. Crie a tabela no banco de dados

```sql
CREATE TABLE messages (
    id        SERIAL PRIMARY KEY,
    username  TEXT NOT NULL,
    message   TEXT NOT NULL,
    sent_at   TIMESTAMP NOT NULL DEFAULT NOW()
);
```

### 4. Configure os `.env`

**`backend/.env` (primário):**
```env
INTERNAL_DATABASE_URL=postgresql://user:senha@host/banco
EXTERNAL_DATABASE_URL=postgresql://user:senha@host-externo/banco
DEBUG=false
SECRET_KEY=chave-longa-e-aleatoria
WS_PORT=9000
HTTP_PORT=5000
IS_REPLICA=false
REPLICA_WS_HOST=127.0.0.1
REPLICA_WS_PORT=9001
```

**`backend/.env.replica` (réplica — copie e ajuste):**
```env
INTERNAL_DATABASE_URL=postgresql://user:senha@host/banco
SECRET_KEY=mesma-chave
WS_PORT=9001
HTTP_PORT=5001
IS_REPLICA=true
```

**`frontend/.env`:**
```env
SECRET_KEY=mesma-chave
# formato: host:wsPort:httpPort
SOCKET_SERVERS=127.0.0.1:9000:5000,127.0.0.1:9001:5001
PORT=8000
```

### 5. Inicie os servidores

**Primário:**
```bash
cd backend
python server.py
```

**Réplica (em outro terminal ou máquina):**
```bash
cd backend
WS_PORT=9001 HTTP_PORT=5001 IS_REPLICA=true python server.py
```

**Frontend:**
```bash
cd frontend
python client.py
```

Acesse **http://localhost:8000**.

---

## Como funciona a replicação

```
Cliente A ──────────► Primário (9000) ──────────► Réplica (9001)
                           │                           │
                     persiste no BD             distribui para
                     (INSERT)                   clientes locais
```

1. O primário tenta se conectar à réplica em loop ao iniciar.
2. Ao conectar, envia `{"type":"__primary__"}` para identificar-se.
3. Cada mensagem de chat é persistida **somente pelo primário** e depois encaminhada para a réplica com o envelope `{"__replica__":true, ...}`.
4. A réplica distribui o frame recebido para seus clientes locais.
5. Se o primário cair, a réplica loga o evento. O frontend detecta a queda e conecta no próximo servidor da lista (`SOCKET_SERVERS`).

---

## Deploy no Render

### Backend (primário)

1. **New → Web Service**, Root Directory: `backend`
2. Build: `pip install -r requirements.txt`
3. Start: `python server.py`
4. Variáveis de ambiente: todas do `backend/.env`

### Backend (réplica)

1. Mesmo repositório, Root Directory: `backend`
2. Start: `python server.py`
3. Variáveis: mesmas, mas com `IS_REPLICA=true`, `WS_PORT` diferente, e `REPLICA_WS_HOST`/`REPLICA_WS_PORT` apontando para o primário

### Frontend

1. Root Directory: `frontend`
2. Start: `python client.py`
3. `SOCKET_SERVERS`: URLs públicas do Render para cada backend, formato `host:wsPort:httpPort`

---

## Estrutura de pastas

```
socketext/
├── backend/
│   ├── server.py
│   ├── requirements.txt
│   └── .env
├── frontend/
│   ├── client.py
│   ├── requirements.txt
│   ├── .env
│   ├── templates/
│   │   ├── login.html
│   │   └── chat.html
│   └── static/
│       ├── script.js
│       └── style.css
└── README.md
```
