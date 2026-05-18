/**
 * SockeText — chat interface
 *
 * Este arquivo gerencia apenas a interface (UI).
 * Para integrar com um servidor WebSocket real, substitua
 * os comentários marcados com "// [SOCKET]" pela lógica de
 * conexão e envio/recebimento de mensagens do seu backend.
 */

(function () {
  /* ── Referências DOM ── */
  const messagesEl = document.getElementById("messages");
  const inputEl    = document.getElementById("msgInput");
  const sendBtn    = document.getElementById("sendBtn");
  const statusDot  = document.getElementById("statusDot");
  const statusLabel = document.getElementById("statusLabel");

  /* ── Estado ── */
  let typingRow = null; // linha do indicador de digitação
  let typingTimeout = null;
  let isTyping = false;


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

  /**
   * Adiciona uma mensagem na área de chat.
   *
   * @param {object} opts
   * @param {"incoming"|"outgoing"} opts.direction
   * @param {string} opts.text       - Texto da mensagem
   * @param {string} [opts.sender]   - Nome do remetente (apenas incoming)
   * @param {string} [opts.time]     - Horário (gerado automaticamente se omitido)
   */
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


  /* ── Resetar todas mensagens após um reconnect ── */
  function removeAllMessages() {
    messagesEl.replaceChildren(); 
  }

  /* ── Indicador de digitação ── */

  function showTypingIndicator(sender) {
    removeTypingIndicator();

    const row = document.createElement("div");
    row.className = "msg-row incoming";

    let senderHTML = sender
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
    if (typingRow) {
      typingRow.remove();
      typingRow = null;
    }
  }


  /* ── Status de conexão ── */

  /**
   * Atualiza o indicador de status no header.
   * @param {"online"|"offline"|"connecting"} state
   */
  function setConnectionStatus(state) {
    const colors = {
      online:     "#3fcf8e",
      offline:    "#e24b4a",
      connecting: "#EF9F27",
    };
    const labels = {
      online:     "ao vivo",
      offline:    "desconectado",
      connecting: "conectando...",
    };

    statusDot.style.background  = colors[state] || colors.online;
    statusLabel.textContent     = labels[state]  || state;
  }


  /* ── Envio de mensagem (interface) ── */

  function sendMessage() {
  const text = inputEl.value.trim();
  if (!text) return;

  // só avisa o servidor se estava digitando de fato
  if (isTyping) {
    clearTimeout(typingTimeout);
    typingTimeout = null;
    isTyping = false;
    if (socket && socket.connected) {
      socket.emit("message", { type: "stop_typing", sender: NAME });
    }
  }

  const messageRow = addMessage({ direction: "outgoing", text });

  inputEl.value = "";
  inputEl.style.height = "auto";

  messagesQueue.push({
    data: { type: "message", sender: NAME, text: text },
    uiRow: messageRow
  });

  messageRow.style.opacity = "0.5";
  processMessage();
}
  
  function processMessage() {
    if (queueWorker !== null) return;
    
    queueWorker = setInterval(() => {
      if (messagesQueue.length === 0) {
        clearInterval(queueWorker)
        queueWorker = null;
        return;
      }

      if (socket && socket.connected) {
        const actualMessage = messagesQueue.shift();

        socket.emit('message', actualMessage.data);
        actualMessage.uiRow.style.opacity = "1";
      }
    }, 2000)

  };

  /* ── Fila de mensagens para garantir ordem de envio ── */
  let messagesQueue = []
  let queueWorker = null

  /* ── Auto-resize do textarea ── */

  function handleTyping() {
    if (socket && socket.connected) {
      if (!isTyping) {
        isTyping = true;
        socket.emit("message", { type: "typing", sender: NAME });
      }

      clearTimeout(typingTimeout);
      typingTimeout = setTimeout(() => {
        isTyping = false;
        if (socket && socket.connected) {
          socket.emit("message", { type: "stop_typing", sender: NAME });
        }
      }, 2000);
    }
  }

  inputEl.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });

  inputEl.addEventListener("input", () => {
    this.style.height = "auto";
    this.style.height = Math.min(this.scrollHeight, 100) + "px";
    handleTyping();
  });
  inputEl.addEventListener("keyup", handleTyping);
  inputEl.addEventListener("compositionend", handleTyping);

  sendBtn.addEventListener("click", sendMessage);


  /* ──────────────────────────────────────────────────────────
     API PÚBLICA
     Exponha estas funções para integrar com seu WebSocket.
     ────────────────────────────────────────────────────────── */

  /**
   * Chame quando receber uma mensagem do servidor.
   * Exemplo de uso:
   *   SockeText.receive({ sender: "Maria", text: "Olá!" });
   */
  window.SockeText = {

    /**
     * Recebe e exibe uma mensagem de outro participante.
     * @param {{ sender: string, text: string, time?: string }} opts
     */
    receive(opts) {
      addMessage({ direction: "incoming", ...opts });
    },

    /**
     * Remove todas mensagens antigas para atualizar tela.
     */
    clearMessages() {
      removeAllMessages();
    },

    /**
     * Exibe o indicador de digitação de outro participante.
     * @param {string} [sender]
     */
    showTyping(sender) {
      showTypingIndicator(sender);
    },

    /**
     * Remove o indicador de digitação.
     */
    hideTyping() {
      removeTypingIndicator();
    },

    /**
     * Atualiza o status de conexão no header.
     * @param {"online"|"offline"|"connecting"} state
     */
    setStatus(state) {
      setConnectionStatus(state);
    },
  };

  function tryConnect(index = 0) { 
    if (index >= SERVERS.length) {
      console.error("Nenhum servidor disponível");
      setTimeout(() => {
        tryConnect()
        console.log('Waiting 2 seconds before trying to find servers...');
      }, 2000)
      return;
    }

    const server = SERVERS[index];

    socket = io(server, {
      timeout: 1000,
      reconnection: false
    });

    socket.on("connect", () => {

      console.log("Conectado:", server);

      SockeText.setStatus("online");
      
    });
    
    socket.on("connect_error", () => {
      
      console.log("Falhou:", server);
      
      SockeText.setStatus("offline");
      
      socket.disconnect();
      
      tryConnect(index + 1);
      
    });
    
    socket.on('history_load', (data) => {
      const pending = [...messagesQueue];

      SockeText.clearMessages()
      data.forEach(element => {
        if (element.sender === NAME) {
          addMessage({ direction: "outgoing", text: element.text, time: element.time });
        }
        else {
          SockeText.receive({ sender: element.sender, text: element.text, time: element.time });
        }
      });

      messagesQueue = [];
      pending.forEach(item => {
        const newRow = addMessage({ direction: "outgoing", text: item.data.text });
        newRow.style.opacity = "0.5";
        messagesQueue.push({ data: item.data, uiRow: newRow });
      });

      if (messagesQueue.length > 0) processMessage();
    });

    socket.on('message', (data) => {
      if (data.type === 'typing') {
        SockeText.showTyping(data.sender);
      } else if (data.type === 'stop_typing') {
        SockeText.hideTyping();
      } else if (data.type === 'message') {
        SockeText.receive({ sender: data.sender, text: data.text });
      }
    });
  
    socket.on('disconnect', (reason) => {
      console.log('Desconectado do servidor! Motivo: ', reason)

      if (queueWorker !== null) {
        clearInterval(queueWorker);
        queueWorker = null;
      }
      tryConnect()
    });
  };
  
  // [SOCKET] Exemplo de integração WebSocket:
  
  var socket = null;

  tryConnect();
})();
