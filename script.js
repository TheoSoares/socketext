/**
 * SockeText — chat interface
 *
 * Este arquivo gerencia apenas a interface (UI).
 * Para integrar com um servidor WebSocket real, substitua
 * os comentários marcados com "// [SOCKET]" pela lógica de
 * conexão e envio/recebimento de mensagens do seu backend.
 */

(function () {
  "use strict";

  /* ── Referências DOM ── */
  const messagesEl = document.getElementById("messages");
  const inputEl    = document.getElementById("msgInput");
  const sendBtn    = document.getElementById("sendBtn");
  const statusDot  = document.getElementById("statusDot");
  const statusLabel = document.getElementById("statusLabel");

  /* ── Estado ── */
  let typingRow = null; // linha do indicador de digitação


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

    // Exibe a mensagem localmente como "outgoing"
    addMessage({ direction: "outgoing", text });

    // Limpa o campo de entrada
    inputEl.value = "";
    inputEl.style.height = "auto";

    // [SOCKET] Aqui você envia a mensagem para o servidor:
    // socket.send(JSON.stringify({ type: "message", text }));
  }


  /* ── Auto-resize do textarea ── */

  inputEl.addEventListener("input", function () {
    this.style.height = "auto";
    this.style.height = Math.min(this.scrollHeight, 100) + "px";
  });

  inputEl.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });

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

  // [SOCKET] Exemplo de integração WebSocket:
  //
  // const socket = new WebSocket("wss://seu-servidor.com/chat");
  //
  // socket.addEventListener("open", () => {
  //   SockeText.setStatus("online");
  // });
  //
  // socket.addEventListener("close", () => {
  //   SockeText.setStatus("offline");
  // });
  //
  // socket.addEventListener("message", (event) => {
  //   const data = JSON.parse(event.data);
  //   if (data.type === "typing") {
  //     SockeText.showTyping(data.sender);
  //   } else if (data.type === "message") {
  //     SockeText.receive({ sender: data.sender, text: data.text });
  //   }
  // });

})();
