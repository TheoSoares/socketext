/**
 * SockeText — script.js
 *
 * Gerencia a interface (UI) e a comunicação com o Web Worker
 * (socket.worker.js) que roda em thread separada.
 *
 * Thread principal  → UI + envio de comandos ao worker
 * socket.worker.js  → thread dedicada à recepção de mensagens WebSocket
 */

(function () {
  /* ── Referências DOM ── */
  const messagesEl  = document.getElementById("messages");
  const inputEl     = document.getElementById("msgInput");
  const sendBtn     = document.getElementById("sendBtn");
  const statusDot   = document.getElementById("statusDot");
  const statusLabel = document.getElementById("statusLabel");

  /* ── Estado ── */
  let typingRow     = null;
  let typingTimeout = null;
  let isTyping      = false;
  let workerReady   = false;   // true quando o worker confirmou conexão

  /* ── Fila de mensagens e worker de intervalo ── */
  let messagesQueue = [];
  let queueWorker   = null;

  /* ────────────────────────────────────────────
     WEB WORKER — thread dedicada à recepção
     O Worker roda em thread separada do browser.
     Toda comunicação é via postMessage / onmessage.
  ──────────────────────────────────────────── */
  const socketWorker = new Worker(
    window.WORKER_URL || "/static/socket.worker.js"
  );

  /* Inicia a conexão dentro do worker assim que o script carrega */
  socketWorker.postMessage({ cmd: 'connect', servers: SERVERS });

  /* Recebe eventos do worker (roda na thread principal, mas a lógica de
     socket fica completamente encapsulada na thread do worker) */
  socketWorker.onmessage = function (e) {
    const msg = e.data;

    switch (msg.event) {

      case 'status':
        setConnectionStatus(msg.state);
        if (msg.state === 'online')  workerReady = true;
        if (msg.state === 'offline') workerReady = false;
        // Processa fila quando ficar online
        if (msg.state === 'online' && messagesQueue.length > 0) processMessage();
        break;

      case 'history_load':
        handleHistoryLoad(msg.data);
        break;

      case 'message':
        handleIncomingMessage(msg.data);
        break;

      case 'log':
        console.log('[Worker]', msg.text);
        break;
    }
  };

  socketWorker.onerror = function (err) {
    console.error('[Worker] Erro não tratado:', err);
  };

  /* ── Helpers ── */

  function getTime() {
    const d = new Date();
    return d.getHours().toString().padStart(2, "0") + ":" +
           d.getMinutes().toString().padStart(2, "0");
  }

  function scrollToBottom() {
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  function escapeHTML(str) {
    return str
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  /* ── Renderização de mensagens ── */

  function addMessage({ direction, text, sender = "", time }) {
    removeTypingIndicator();

    const row = document.createElement("div");
    row.className = `msg-row ${direction}`;

    let senderHTML = "";
    if (direction === "incoming" && sender) {
      senderHTML = `<div class="msg-sender">${escapeHTML(sender)}</div>`;
    }

    row.innerHTML = `
      ${senderHTML}
      <div class="msg-bubble">${escapeHTML(text)}</div>
      <div class="msg-time">${time || getTime()}</div>
    `;

    messagesEl.appendChild(row);
    scrollToBottom();
    return row;
  }

  function removeAllMessages() {
    messagesEl.replaceChildren();
  }

  /* ── Indicador de digitação ── */

  function showTypingIndicator(sender) {
    removeTypingIndicator();
    const row = document.createElement("div");
    row.className = "msg-row incoming";
    const senderHTML = sender
      ? `<div class="msg-sender">${escapeHTML(sender)}</div>`
      : "";
    row.innerHTML = `
      ${senderHTML}
      <div class="typing-indicator">
        <div class="typing-dot"></div>
        <div class="typing-dot"></div>
        <div class="typing-dot"></div>
      </div>
    `;
    typingRow = row;
    messagesEl.appendChild(row);
    scrollToBottom();
  }

  function removeTypingIndicator() {
    if (typingRow) { typingRow.remove(); typingRow = null; }
  }

  /* ── Status de conexão ── */

  function setConnectionStatus(state) {
    const colors = { online: "#3fcf8e", offline: "#e24b4a", connecting: "#EF9F27" };
    const labels = { online: "ao vivo",  offline: "desconectado", connecting: "conectando..." };
    statusDot.style.background = colors[state] || colors.online;
    statusLabel.textContent    = labels[state]  || state;
  }

  /* ── Handlers de eventos vindos do worker ── */

  function handleHistoryLoad(data) {
    const pending = [...messagesQueue];
    removeAllMessages();

    data.forEach(element => {
      if (element.sender === NAME) {
        addMessage({ direction: "outgoing", text: element.text, time: element.time });
      } else {
        addMessage({ direction: "incoming", sender: element.sender, text: element.text, time: element.time });
      }
    });

    messagesQueue = [];
    pending.forEach(item => {
      const newRow = addMessage({ direction: "outgoing", text: item.data.text });
      newRow.style.opacity = "0.5";
      messagesQueue.push({ data: item.data, uiRow: newRow });
    });

    if (messagesQueue.length > 0) processMessage();
  }

  function handleIncomingMessage(data) {
    if (data.type === 'typing') {
      showTypingIndicator(data.sender);
    } else if (data.type === 'stop_typing') {
      removeTypingIndicator();
    } else if (data.type === 'message') {
      addMessage({ direction: "incoming", sender: data.sender, text: data.text });
    }
  }

  /* ── Envio de mensagem ── */

  function sendMessage() {
    const text = inputEl.value.trim();
    if (!text) return;

    if (isTyping) {
      clearTimeout(typingTimeout);
      typingTimeout = null;
      isTyping = false;
      if (workerReady) {
        socketWorker.postMessage({ cmd: 'send', data: { type: "stop_typing", sender: NAME } });
      }
    }

    const messageRow = addMessage({ direction: "outgoing", text });
    inputEl.value = "";
    inputEl.style.height = "auto";

    messagesQueue.push({
      data:   { type: "message", sender: NAME, text },
      uiRow:  messageRow
    });
    messageRow.style.opacity = "0.5";
    processMessage();
  }

  function processMessage() {
    if (queueWorker !== null) return;

    queueWorker = setInterval(() => {
      if (messagesQueue.length === 0) {
        clearInterval(queueWorker);
        queueWorker = null;
        return;
      }
      if (workerReady) {
        const item = messagesQueue.shift();
        // Envia pelo worker (que tem acesso ao socket na sua thread)
        socketWorker.postMessage({ cmd: 'send', data: item.data });
        item.uiRow.style.opacity = "1";
      }
    }, 200);   // intervalo reduzido: 200 ms (antes 2000) — a fila serve para ordem, não delay
  }

  /* ── Indicador de digitação (envio) ── */

  function handleTyping() {
    if (!workerReady) return;

    if (!isTyping) {
      isTyping = true;
      socketWorker.postMessage({ cmd: 'send', data: { type: "typing", sender: NAME } });
    }

    clearTimeout(typingTimeout);
    typingTimeout = setTimeout(() => {
      isTyping = false;
      if (workerReady) {
        socketWorker.postMessage({ cmd: 'send', data: { type: "stop_typing", sender: NAME } });
      }
    }, 2000);
  }

  /* ── Eventos do input ── */

  inputEl.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });

  inputEl.addEventListener("input", function () {
    this.style.height = "auto";
    this.style.height = Math.min(this.scrollHeight, 100) + "px";
    handleTyping();
  });

  inputEl.addEventListener("keyup",          handleTyping);
  inputEl.addEventListener("compositionend", handleTyping);
  sendBtn.addEventListener("click",          sendMessage);

})();
