# ★ SockeText

Chat em tempo real com suporte a múltiplos servidores WebSocket, histórico persistente e indicador de digitação.

Construído com **Flask + Flask-SocketIO** no backend, **Flask** no frontend e **PostgreSQL** como banco de dados. Utiliza **Redis** como message queue para sincronizar mensagens entre instâncias do servidor.

---

## Arquitetura

```
frontend/          → Cliente Flask (porta definida por PORT)
│  client.py       → Rotas HTTP (login, chat)
│  requirements.txt
│  templates/      → HTML (login.html, chat.html)
│  static/         → JS e CSS da interface
│
backend/           → Servidor WebSocket (porta definida por PORT)
│  server.py       → SocketIO: histórico, mensagens, typing
│  requirements.txt
```

A comunicação entre frontend e backend acontece via **WebSocket (Socket.IO)**. O frontend tenta se conectar a cada servidor listado em `SOCKET_SERVERS` em sequência, reconectando automaticamente em caso de queda.

---

## Funcionalidades

- Mensagens em tempo real via WebSocket
- Histórico de mensagens persistido em banco de dados (carregado ao conectar)
- Indicador de "digitando..." para outros participantes
- Fila de envio garantindo ordem das mensagens
- Fallback automático entre múltiplos servidores WebSocket
- Interface responsiva em português

---

## Requisitos

- Python 3.10+
- PostgreSQL
- Redis

---

## Instalação

### 1. Clone o repositório

```bash
git clone https://github.com/seu-usuario/socketext.git
cd socketext
```

### 2. Instale as dependências

**Backend:**
```bash
cd backend
pip install -r requirements.txt
```

**Frontend:**
```bash
cd frontend
pip install -r requirements.txt
```

### 3. Configure os arquivos `.env`

Crie um arquivo `.env` em cada pasta conforme as seções abaixo.

### 4. Crie a tabela no banco de dados

```sql
CREATE TABLE messages (
    id        SERIAL PRIMARY KEY,
    username  TEXT NOT NULL,
    message   TEXT NOT NULL,
    sent_at   TIMESTAMP NOT NULL DEFAULT NOW()
);
```

### 5. Inicie os servidores

**Backend** (produção com Gunicorn + gevent):
```bash
cd backend
gunicorn -k geventwebsocket.gunicorn.workers.GeventWebSocketWorker -w 1 -b 0.0.0.0:$PORT server:app
```

**Backend** (desenvolvimento local):
```bash
cd backend
python server.py
```

**Frontend:**
```bash
cd frontend
python client.py
```

Acesse **http://localhost:8000**, digite seu nome e comece a conversar.

---

## Configuração do `.env`

### `backend/.env`

| Variável | Descrição |
|---|---|
| `INTERNAL_DATABASE_URL` | URL de conexão PostgreSQL usada em produção (rede interna) |
| `EXTERNAL_DATABASE_URL` | URL de conexão PostgreSQL usada em modo DEBUG (acesso externo) |
| `DEBUG` | `true` usa `EXTERNAL_DATABASE_URL`; `false` usa a interna |
| `SECRET_KEY` | Chave secreta do Flask — use uma string longa e aleatória |
| `REDIS_URL` | URL de conexão Redis usada como message queue pelo SocketIO |
| `PORT` | Porta em que o servidor WebSocket irá escutar (ex: `5000`) |

**Exemplo:**
```env
INTERNAL_DATABASE_URL=postgresql://user:senha@host-interno/nome_do_banco
EXTERNAL_DATABASE_URL=postgresql://user:senha@host-externo/nome_do_banco
DEBUG=false
SECRET_KEY=troque-por-uma-chave-segura-e-aleatoria
REDIS_URL=redis://default:senha@host-redis:porta
PORT=5000
```

---

### `frontend/.env`

| Variável | Descrição |
|---|---|
| `SECRET_KEY` | Mesma chave usada no backend (para sessões Flask) |
| `SOCKET_SERVERS` | Lista de URLs dos servidores WebSocket, separadas por vírgula |
| `PORT` | Porta em que o frontend irá escutar (ex: `8000`) |

**Exemplo:**
```env
SECRET_KEY=troque-por-uma-chave-segura-e-aleatoria
SOCKET_SERVERS=http://192.168.1.10:5000,http://192.168.1.10:5001
PORT=8000
```

O frontend tenta conectar aos servidores na ordem listada. Se o primeiro falhar, tenta o próximo automaticamente.

---

## Deploy no Render

### Backend

1. **New → Web Service**, conecte ao repositório
2. **Root Directory:** `backend`
3. **Build Command:** `pip install -r requirements.txt`
4. **Start Command:**
   ```bash
   gunicorn -k geventwebsocket.gunicorn.workers.GeventWebSocketWorker -w 1 -b 0.0.0.0:$PORT server:app
   ```
5. Em **Environment Variables**, adicione as variáveis do `backend/.env`. O Render injeta `PORT` automaticamente — não é necessário definir manualmente.

### Frontend

1. **New → Web Service**, conecte ao repositório
2. **Root Directory:** `frontend`
3. **Build Command:** `pip install -r requirements.txt`
4. **Start Command:** `python client.py`
5. Em **Environment Variables**, adicione as variáveis do `frontend/.env`. Em `SOCKET_SERVERS`, use a URL pública gerada pelo Render para cada instância do backend (ex: `https://socketext-backend.onrender.com`).

> O `-w 1` no Gunicorn é obrigatório com WebSockets — múltiplos workers quebrariam as conexões persistentes. Para escalar, suba múltiplos serviços apontando para o mesmo Redis.

---

## Rodando múltiplas instâncias do backend

Todas as instâncias devem apontar para o **mesmo Redis** — ele sincroniza as mensagens entre elas independentemente de qual instância o cliente estiver conectado.

```bash
# Instância 1 (usa PORT=5000 do .env)
gunicorn -k geventwebsocket.gunicorn.workers.GeventWebSocketWorker -w 1 -b 0.0.0.0:$PORT server:app

# Instância 2
PORT=5001 gunicorn -k geventwebsocket.gunicorn.workers.GeventWebSocketWorker -w 1 -b 0.0.0.0:$PORT server:app
```

Liste todas as URLs no `SOCKET_SERVERS` do frontend.

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
