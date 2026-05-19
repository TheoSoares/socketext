# ★ SockeText

Chat em tempo real com suporte a múltiplos usuários, histórico persistente e failover automático entre servidores.

## Arquitetura

```
[Browser]
    │  WebSocket (Socket.IO)
    ▼
[Frontend — Flask + SocketIO]        porta 8000
    │  TCP socket (cliente manual)
    │  Thread de recepção dedicada   ← requisito
    │  tryConnect: fallback automático entre servidores
    ▼
[Backend A — TCP Server]   [Backend B — TCP Server]
    porta 5000                   porta 5000
    host-1                       host-2
         │                            │
         └──────────┬─────────────────┘
                    ▼
              PostgreSQL
          (histórico de mensagens)
```

### Por que essa arquitetura atende os requisitos

| Requisito | Implementação |
|---|---|
| Thread por conexão no servidor | `backend/server.py` — `threading.Thread` instanciada em `accept_loop()` para cada frontend conectado |
| Thread de recepção no cliente | `frontend/client.py` — `BackendConnection._receive_loop()` roda em thread dedicada bloqueada em `recv()` |
| Tolerância a falhas | `frontend/client.py` — `tryConnect` tenta cada servidor da lista em ordem; se todos falharem, aguarda 2s e reinicia |
| Interface web | Flask serve HTML/CSS/JS; browser usa Socket.IO |

---

## Estrutura de arquivos

```
socketext/
├── backend/
│   ├── server.py          # Servidor TCP: aceita conexões, threads por cliente, broadcast, PostgreSQL
│   ├── requirements.txt
│   └── .env.example
├── frontend/
│   ├── client.py          # Flask HTTP + cliente TCP com thread de recepção e tryConnect
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

### Servidor A (host-1)

```bash
cd backend
PORT=5000 python server.py
```

### Servidor B (host-2)

```bash
cd backend
PORT=5000 python server.py
```

> Ambos os servidores são idênticos e independentes — não há primário nem réplica.  
> Cada um persiste mensagens no mesmo banco PostgreSQL compartilhado.

### Frontend

```bash
cd frontend
python client.py
```

Acesse **http://localhost:8000**, digite seu nome e comece a conversar.

---

## Como funciona o failover

O frontend mantém uma lista ordenada de servidores backend em `BACKEND_SERVERS`.  
Ao conectar (ou reconectar após queda), o `tryConnect` percorre a lista em ordem:

1. Tenta o primeiro servidor com timeout de 3s.
2. Se conectar, envia o handshake e inicia a thread de recepção.
3. Se a conexão cair, tenta o próximo servidor da lista.
4. Se esgotar a lista sem sucesso, aguarda 2s e reinicia do início.

O browser recebe uma notificação via Socket.IO em cada mudança de estado, e o histórico é recarregado automaticamente ao reconectar.

---

## Variáveis de ambiente

### `backend/.env`

| Variável | Descrição | Padrão |
|---|---|---|
| `PORT` | Porta TCP do servidor | `5000` |
| `INTERNAL_DATABASE_URL` | PostgreSQL em produção | — |
| `EXTERNAL_DATABASE_URL` | PostgreSQL em debug local | — |
| `DEBUG` | `true` usa a URL externa | `false` |
| `HISTORY_LIMIT` | Número de mensagens no histórico inicial | `50` |
| `SECRET_KEY` | Chave secreta (usada pelo Flask no frontend) | — |

### `frontend/.env`

| Variável | Descrição | Padrão |
|---|---|---|
| `SECRET_KEY` | Chave secreta Flask | — |
| `BACKEND_SERVERS` | Endereços TCP separados por vírgula | `localhost:5000` |
| `PORT` | Porta HTTP do frontend | `8000` |

**Exemplo com dois servidores:**
```env
BACKEND_SERVERS=host-1.exemplo.com:5000,host-2.exemplo.com:5000
```

---

## Protocolo TCP (backend ↔ frontend)

Todas as mensagens são JSON delimitadas por `\n`.

### Handshake (frontend → backend)
```json
{"type": "join", "username": "Alice"}
```

### Histórico (backend → frontend, logo após handshake)
```json
{
  "type": "history",
  "messages": [
    {"sender": "Alice", "text": "Olá!", "time": "14:32"}
  ]
}
```

### Mensagem (bidirecional)
```json
{"type": "message", "sender": "Alice", "text": "Olá!"}
```

### Digitando (frontend → backend → outros frontends)
```json
{"type": "typing"}
{"type": "stop_typing"}
```

### Sistema (backend → frontend)
```json
{"type": "system", "text": "Alice entrou no chat."}
```
