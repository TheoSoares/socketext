/**
 * socket.worker.js — SockeText
 *
 * Web Worker dedicado exclusivamente à recepção de mensagens do servidor
 * WebSocket. Roda em uma thread separada do browser (Web Workers API),
 * satisfazendo o requisito: "o cliente deve instanciar uma thread dedicada
 * à recepção de mensagens".
 *
 * Comunicação com o script principal (script.js):
 *   - Recebe comandos via onmessage  (script.js → worker)
 *   - Envia  eventos  via postMessage(worker → script.js)
 *
 * Protocolo de mensagens (script.js → worker):
 *   { cmd: 'connect',    servers: string[] }   — inicia conexão
 *   { cmd: 'send',       data: object }         — envia mensagem ao servidor
 *   { cmd: 'disconnect' }                        — encerra conexão
 *
 * Protocolo de eventos (worker → script.js):
 *   { event: 'status',       state: 'online'|'offline'|'connecting' }
 *   { event: 'history_load', data:  object[] }
 *   { event: 'message',      data:  object   }
 *   { event: 'log',          text:  string   }
 */

// O Socket.IO não está disponível como módulo ES no CDN padrão.
// Importamos via importScripts (suportado em Dedicated Workers).
importScripts('https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.0.1/socket.io.js');

let socket   = null;
let servers  = [];

/* ── Utilitário de log ── */
function log(msg) {
  postMessage({ event: 'log', text: msg });
}

/* ── Conexão com fallback entre servidores ── */
function tryConnect(index) {
  if (index === undefined) index = 0;

  if (index >= servers.length) {
    log('Nenhum servidor disponível. Tentando novamente em 2 s...');
    postMessage({ event: 'status', state: 'connecting' });
    setTimeout(function () { tryConnect(0); }, 2000);
    return;
  }

  var url = servers[index];
  log('Tentando conectar: ' + url);
  postMessage({ event: 'status', state: 'connecting' });

  socket = io(url, { 
    timeout: 3000, 
    reconnection: false,
    transports: ['websocket'], // <--- FORÇA apenas WebSocket direto
    upgrade: false             // <--- Bloqueia tentativa de HTTP Polling
  });

  /* ── Conectou ── */
  socket.on('connect', function () {
    log('Conectado: ' + url);
    postMessage({ event: 'status', state: 'online' });
  });

  /* ── Erro de conexão → próximo servidor ── */
  socket.on('connect_error', function () {
    log('Falhou: ' + url);
    postMessage({ event: 'status', state: 'offline' });
    socket.disconnect();
    tryConnect(index + 1);
  });

  /* ── Histórico ── */
  socket.on('history_load', function (data) {
    postMessage({ event: 'history_load', data: data });
  });

  /* ── Mensagem / typing ── */
  socket.on('message', function (data) {
    postMessage({ event: 'message', data: data });
  });

  /* ── Desconexão ── */
  socket.on('disconnect', function (reason) {
    log('Desconectado. Motivo: ' + reason);
    postMessage({ event: 'status', state: 'offline' });
    tryConnect(0);
  });
}

/* ── Recebe comandos do script principal ── */
onmessage = function (e) {
  var msg = e.data;

  if (msg.cmd === 'connect') {
    servers = msg.servers;
    tryConnect(0);

  } else if (msg.cmd === 'send') {
    if (socket && socket.connected) {
      socket.emit('message', msg.data);
    } else {
      log('Tentativa de envio sem conexão ativa — mensagem descartada pelo worker.');
    }

  } else if (msg.cmd === 'disconnect') {
    if (socket) {
      socket.disconnect();
      socket = null;
    }
  }
};
