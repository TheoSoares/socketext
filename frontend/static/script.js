/**
 * SockeText — script.js
 *
 * Protocolo: TCP puro com framing binário (4 bytes big-endian + JSON UTF-8).
 * O navegador não abre sockets TCP diretamente, então usamos um proxy WebSocket
 * leve no backend que faz bridging TCP↔WS.
 *
 * Modelo de threads (emulado com Web Workers + MessageChannel):
 *   - Main thread   → UI, envio de mensagens, fila de envio
 *   - Recv Worker   → recebe frames do socket, posta mensagens para a main thread
 *
 * Tolerância a falhas:
 *   - Lista de servidores em SERVERS (primário primeiro)
 *   - Se o primário falhar, tenta o próximo automaticamente
 *   - Ao reconectar, recarrega histórico e reenfileira mensagens pendentes
 */

(function () {
  "use strict";

  /* ── Constantes de protocolo ────────────────────────────────────────────── */
  const HEADER_SIZE   = 4;          // bytes
  const RECONNECT_MS  = 2000;
  const TYPING_MS     = 2000;
  const HEARTBEAT_MS  = 5000;

  /* ── DOM ────────────────────────────────────────────────────────────────── */
  const messagesEl  = document.getElementById("messages");
  const inputEl     = document.getElementById("msgInput");
  const sendBtn     = document.getElementById("sendBtn");
  const statusDot   = document.getElementById("statusDot");
  const statusLabel = document.getElementById("statusLabel");

  /* ── Estado ─────────────────────────────────────────────────────────────── */
  let socket        = null;
  let serverIndex   = 0;
  let typingRow     = null;
  let typingTimeout = null;
  let isTyping      = false;
  let recvWorker    = null;    // Web Worker dedicado à recepção
  let heartbeatInterval = null;

  // Fila de mensagens com confirmação pendente
  let sendQueue  = [];   // [{data, uiRow}]
  let queueTimer = null;

  /* ── Framing binário ─────────────────────────────────────────────────────
   * Como o navegador usa WebSocket (não TCP puro), aproveitamos o próprio
   * frame WebSocket como envelope — cada send() carrega exatamente um JSON.
   * O servidor espera: [4 bytes big-endian length][payload UTF-8].
   * O worker de recepção reconstrui o mesmo formato no lado cliente.
   * ────────────────────────────────────────────────────────────────────── */

  function encodeFrame(payload) {
    const json  = JSON.stringify(payload);
    const bytes = new TextEncoder().encode(json);
    const buf   = new ArrayBuffer(HEADER_SIZE + bytes.byteLength);
    const view  = new DataView(buf);
    view.setUint32(0, bytes.byteLength, false);   // big-endian
    new Uint8Array(buf).set(bytes, HEADER_SIZE);
    return buf;
  }

  function decodeFrame(buf) {
    const view   = new DataView(buf);
    const length = view.getUint32(0, false);
    const bytes  = new Uint8Array(buf, HEADER_SIZE, length);
    return JSON.parse(new TextDecoder().decode(bytes));
  }

  /* ── Web Worker inline (thread de recepção) ──────────────────────────────
   * O worker roda em thread separada do navegador.
   * Ele recebe mensagens do socket e as posta para a main thread.
   * Criado como Blob URL para não precisar de arquivo externo.
   * ────────────────────────────────────────────────────────────────────── */

  const WORKER_SRC = `
    const HEADER_SIZE = 4;
    let ws = null;

    self.onmessage = function(e) {
      const { cmd, url } = e.data;
      if (cmd === "connect") {
        if (ws) { try { ws.close(); } catch(_) {} }
        ws = new WebSocket(url);
        ws.binaryType = "arraybuffer";

        ws.onopen    = () => self.postMessage({ event: "open" });
        ws.onclose   = (ev) => self.postMessage({ event: "close", code: ev.code, reason: ev.reason });
        ws.onerror   = () => self.postMessage({ event: "error" });
        ws.onmessage = (ev) => {
          try {
            const view   = new DataView(ev.data);
            const length = view.getUint32(0, false);
            const bytes  = new Uint8Array(ev.data, HEADER_SIZE, length);
            const json   = JSON.parse(new TextDecoder().decode(bytes));
            self.postMessage({ event: "message", data: json });
          } catch(err) {
            self.postMessage({ event: "parse_error", err: String(err) });
          }
        };
      } else if (cmd === "send") {
        if (ws && ws.readyState === 1) {
          const json  = JSON.stringify(e.data.payload);
          const bytes = new TextEncoder().encode(json);
          const buf   = new ArrayBuffer(HEADER_SIZE + bytes.byteLength);
          const view  = new DataView(buf);
          view.setUint32(0, bytes.byteLength, false);
          new Uint8Array(buf).set(bytes, HEADER_SIZE);
          ws.send(buf);
        }
      } else if (cmd === "close") {
        if (ws) { try { ws.close(); } catch(_) {} ws = null; }
      }
    };
  `;

  function createRecvWorker() {
    const blob = new Blob([WORKER_SRC], { type: "application/javascript" });
    const url  = URL.createObjectURL(blob);
    const w    = new Worker(url);
    URL.revokeObjectURL(url);
    return w;
  }

  /* ── Conexão e reconexão ─────────────────────────────────────────────────
   * Tenta cada servidor da lista em sequência.
   * O backend expõe um endpoint WebSocket que faz bridge para o TCP interno.
   * URL: ws://host:wsPort/ws
   * ────────────────────────────────────────────────────────────────────── */

  function connect(index) {
    if (index >= SERVERS.length) {
      setStatus("offline");
      console.warn("Nenhum servidor disponível. Tentando novamente…");
      setTimeout(() => connect(0), RECONNECT_MS);
      return;
    }

    serverIndex = index;
    const srv = SERVERS[index];
    const url = `ws://${srv.host}:${srv.wsPort}/ws`;

    setStatus("connecting");
    console.log(`Conectando ao servidor ${index}: ${url}`);

    if (recvWorker) {
      recvWorker.terminate();
    }
    recvWorker = createRecvWorker();

    recvWorker.onmessage = function(e) {
      const msg = e.data;
      switch (msg.event) {
        case "open":
          onConnected(srv);
          break;
        case "message":
          onMessage(msg.data);
          break;
        case "close":
        case "error":
          onDisconnected(index);
          break;
        case "parse_error":
          console.error("Erro de parse:", msg.err);
          break;
      }
    };

    recvWorker.postMessage({ cmd: "connect", url });
  }

  function sendFrame(payload) {
    if (recvWorker) {
      recvWorker.postMessage({ cmd: "send", payload });
    }
  }

  function onConnected(srv) {
    console.log("✓ Conectado:", srv);
    setStatus("online");

    // Identifica-se ao servidor
    sendFrame({ type: "join", sender: NAME });

    // Heartbeat para manter conexão ativa
    if (heartbeatInterval) clearInterval(heartbeatInterval);
    heartbeatInterval = setInterval(() => {
      sendFrame({ type: "__heartbeat__" });
    }, HEARTBEAT_MS);
  }

  function onDisconnected(failedIndex) {
    console.warn("Desconectado do servidor", failedIndex);
    setStatus("offline");
    if (heartbeatInterval) clearInterval(heartbeatInterval);
    if (queueTimer) { clearInterval(queueTimer); queueTimer = null; }

    // Tenta próximo servidor
    connect(failedIndex + 1);
  }

  /* ── Tratamento de mensagens recebidas ───────────────────────────────────
   * Roda na main thread (postado pelo worker de recepção).
   * ────────────────────────────────────────────────────────────────────── */

  function onMessage(data) {
    const type = data.type;

    if (type === "history_load") {
      loadHistory(data.data);
      return;
    }
    if (type === "typing") {
      showTypingIndicator(data.sender);
      return;
    }
    if (type === "stop_typing") {
      removeTypingIndicator();
      return;
    }
    if (type === "message") {
      addMessage({ direction: "incoming", sender: data.sender, text: data.text, time: data.time });
      return;
    }
    if (type === "__heartbeat__") return;   // keep-alive do servidor, ignora
  }

  function loadHistory(items) {
    clearMessages();
    const pendingTexts = sendQueue.map(q => q.data.text);

    items.forEach(item => {
      const dir = item.sender === NAME ? "outgoing" : "incoming";
      addMessage({ direction: dir, sender: item.sender, text: item.text, time: item.time });
    });

    // Reinsere mensagens pendentes na fila com novas linhas de UI
    const pending = [...sendQueue];
    sendQueue = [];
    pending.forEach(item => {
      const newRow = addMessage({ direction: "outgoing", text: item.data.text });
      newRow.style.opacity = "0.5";
      sendQueue.push({ data: item.data, uiRow: newRow });
    });

    if (sendQueue.length > 0) processQueue();
  }

  /* ── Fila de envio ───────────────────────────────────────────────────────
   * Garante ordem de entrega mesmo em reconexões.
   * ────────────────────────────────────────────────────────────────────── */

  function processQueue() {
    if (queueTimer !== null) return;
    queueTimer = setInterval(() => {
      if (sendQueue.length === 0) {
        clearInterval(queueTimer);
        queueTimer = null;
        return;
      }
      const item = sendQueue.shift();
      sendFrame(item.data);
      item.uiRow.style.opacity = "1";
    }, 300);
  }

  /* ── Envio de mensagem ───────────────────────────────────────────────────*/

  function sendMessage() {
    const text = inputEl.value.trim();
    if (!text) return;

    // Para o indicador de digitação
    if (isTyping) {
      clearTimeout(typingTimeout);
      typingTimeout = null;
      isTyping = false;
      sendFrame({ type: "stop_typing", sender: NAME });
    }

    const uiRow = addMessage({ direction: "outgoing", text });
    uiRow.style.opacity = "0.5";

    inputEl.value = "";
    inputEl.style.height = "auto";

    sendQueue.push({ data: { type: "message", sender: NAME, text }, uiRow });
    processQueue();
  }

  /* ── Indicador de digitação ──────────────────────────────────────────────*/

  function handleTyping() {
    if (!isTyping) {
      isTyping = true;
      sendFrame({ type: "typing", sender: NAME });
    }
    clearTimeout(typingTimeout);
    typingTimeout = setTimeout(() => {
      isTyping = false;
      sendFrame({ type: "stop_typing", sender: NAME });
    }, TYPING_MS);
  }

  /* ── Renderização ────────────────────────────────────────────────────────*/

  function getTime() {
    const d = new Date();
    return String(d.getHours()).padStart(2,"0") + ":" + String(d.getMinutes()).padStart(2,"0");
  }

  function escapeHTML(s) {
    return String(s)
      .replace(/&/g,"&amp;").replace(/</g,"&lt;")
      .replace(/>/g,"&gt;").replace(/"/g,"&quot;");
  }

  function addMessage({ direction, text, sender = "", time }) {
    removeTypingIndicator();
    const row = document.createElement("div");
    row.className = `msg-row ${direction}`;
    const senderHTML = (direction === "incoming" && sender)
      ? `<div class="msg-sender">${escapeHTML(sender)}</div>` : "";
    row.innerHTML = `
      ${senderHTML}
      <div class="msg-bubble">${escapeHTML(text)}</div>
      <div class="msg-time">${time || getTime()}</div>
    `;
    messagesEl.appendChild(row);
    messagesEl.scrollTop = messagesEl.scrollHeight;
    return row;
  }

  function clearMessages() {
    messagesEl.replaceChildren();
    const divider = document.createElement("div");
    divider.className = "date-divider";
    divider.innerHTML = "<span>Hoje</span>";
    messagesEl.appendChild(divider);
  }

  function showTypingIndicator(sender) {
    removeTypingIndicator();
    const row = document.createElement("div");
    row.className = "msg-row incoming";
    row.innerHTML = `
      ${sender ? `<div class="msg-sender">${escapeHTML(sender)}</div>` : ""}
      <div class="typing-indicator">
        <div class="typing-dot"></div>
        <div class="typing-dot"></div>
        <div class="typing-dot"></div>
      </div>
    `;
    typingRow = row;
    messagesEl.appendChild(row);
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  function removeTypingIndicator() {
    if (typingRow) { typingRow.remove(); typingRow = null; }
  }

  function setStatus(state) {
    const colors = { online:"#3fcf8e", offline:"#e24b4a", connecting:"#EF9F27" };
    const labels = { online:"ao vivo", offline:"desconectado", connecting:"conectando…" };
    statusDot.style.background = colors[state] || colors.offline;
    statusLabel.textContent    = labels[state]  || state;
  }

  /* ── Eventos DOM ─────────────────────────────────────────────────────────*/

  inputEl.addEventListener("keydown", e => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMessage(); }
  });
  inputEl.addEventListener("input", function() {
    this.style.height = "auto";
    this.style.height = Math.min(this.scrollHeight, 100) + "px";
    handleTyping();
  });
  sendBtn.addEventListener("click", sendMessage);

  /* ── Inicia ──────────────────────────────────────────────────────────────*/
  connect(0);

})();
