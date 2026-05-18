# ★ SockeText

Chat em tempo real com suporte a múltiplos servidores WebSocket, histórico persistente e indicador de digitação.

Construído com **Flask + Flask-SocketIO** no backend, **Flask** no frontend e **PostgreSQL** como banco de dados. Utiliza **Redis** como message queue para sincronizar mensagens entre instâncias do servidor.

---

## Arquitetura

```
frontend/          → Cliente Flask (porta 8000)
│  client.py       → Rotas HTTP (login, chat)
│  templates/      → HTML (login.html, chat.html)
│  static/         → JS e CSS da interface
│
backend/           → Servidor WebSocket (porta 5000+)
│  server.py       → SocketIO: histórico, mensagens, typing
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
pip install flask flask-socketio flask-sqlalchemy psycopg2-binary python-dotenv
```

**Frontend:**
```bash
cd frontend
pip install flask python-dotenv
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

```bash
# Terminal 1 — backend
cd backend
python server.py

# Terminal 2 — frontend
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
| `PORT` | Porta em que o servidor WebSocket irá escutar (ex: `8000`) |

**Exemplo:**
```env
SECRET_KEY=troque-por-uma-chave-segura-e-aleatoria
SOCKET_SERVERS=http://192.168.1.10:5000,http://192.168.1.10:5001
```

O frontend tenta conectar aos servidores na ordem listada. Se o primeiro falhar, tenta o próximo automaticamente.

---

## Rodando múltiplas instâncias do backend

Para escalar horizontalmente, rode várias instâncias do `server.py` em portas diferentes. Todas devem apontar para o **mesmo Redis** — ele garante que as mensagens sejam entregues a todos os clientes, independentemente de qual instância eles estejam conectados.

Defina `PORT` no `.env` de cada instância e suba normalmente. Em ambientes onde só existe um `.env`, você pode sobrescrever a variável na linha de comando:

```bash
# Instância 1 (usa PORT=5000 do .env)
python server.py

# Instância 2
PORT=5001 python server.py
```

Liste todas as URLs no `SOCKET_SERVERS` do frontend.

---

## Estrutura de pastas

```
socketext/
├── backend/
│   ├── server.py
│   └── .env
├── frontend/
│   ├── client.py
│   ├── .env
│   ├── templates/
│   │   ├── login.html
│   │   └── chat.html
│   └── static/
│       ├── script.js
│       └── style.css
└── README.md
```
