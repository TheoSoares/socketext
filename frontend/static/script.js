/**
 * script.js — Lógica do cliente web do SockeText.
 *
 * Responsabilidades:
 *   - Conectar ao frontend Flask via Socket.IO.
 *   - Enviar mensagens e sinalizar digitação.
 *   - Renderizar mensagens, histórico e notificações do sistema.
 *   - Gerenciar indicador de "digitando...".
 */

"use strict";

// ---------------------------------------------------------------------------
// Conexão Socket.IO
// ---------------------------------------------------------------------------

const socket = io({ transports: ["websocket"] });

// ---------------------------------------------------------------------------
// Referências DOM
// ---------------------------------------------------------------------------

const messagesList = document.getElementById("messages");
const messageInput = document.getElementById("message-input");
const sendBtn      = document.getElementById("send-btn");
const typingEl     = document.getElementById("typing-indicator");

// ---------------------------------------------------------------------------
// Estado local
// ---------------------------------------------------------------------------

/** Usuários digitando no momento: Set<string> */
const typingUsers = new Set();

/** Timer para parar de sinalizar "digitando" após inatividade */
let typingTimer = null;

// ---------------------------------------------------------------------------
// Renderização
// ---------------------------------------------------------------------------

/**
 * Cria e insere uma bolha de mensagem na lista.
 *
 * @param {string} username
 * @param {string} text
 * @param {string} [sentAt]   - Timestamp ISO opcional.
 * @param {boolean} [isOwn]   - True se é mensagem do próprio usuário.
 */
function appendMessage(username, text, sentAt = "", isOwn = false) {
  const time = sentAt
    ? new Date(sentAt).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" })
    : new Date().toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });

  const item = document.createElement("div");
  item.classList.add("msg", isOwn ? "msg--own" : "msg--other");

  item.innerHTML = `
    <div class="msg__header">
      <span class="msg__user">${escapeHtml(username)}</span>
      <span class="msg__time">${time}</span>
    </div>
    <div class="msg__bubble">${escapeHtml(text)}</div>
  `;

  messagesList.appendChild(item);
  scrollToBottom();
}

/**
 * Insere uma mensagem de sistema (entrada/saída, reconexão, etc.).
 *
 * @param {string} text
 */
function appendSystemMessage(text) {
  const item = document.createElement("div");
  item.classList.add("msg", "msg--system");
  item.innerHTML = `<div class="msg__bubble">${escapeHtml(text)}</div>`;
  messagesList.appendChild(item);
  scrollToBottom();
}

/** Rola a lista para o final. */
function scrollToBottom() {
  messagesList.scrollTop = messagesList.scrollHeight;
}

/**
 * Escapa caracteres HTML para evitar XSS.
 *
 * @param {string} str
 * @returns {string}
 */
function escapeHtml(str) {
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// ---------------------------------------------------------------------------
// Indicador de digitação
// ---------------------------------------------------------------------------

/** Atualiza o texto do indicador com base em typingUsers. */
function updateTypingIndicator() {
  const users = [...typingUsers].filter(u => u !== window.CURRENT_USER);

  if (users.length === 0) {
    typingEl.hidden = true;
    typingEl.textContent = "";
    return;
  }

  typingEl.hidden = false;
  typingEl.textContent =
    users.length === 1
      ? `${users[0]} está digitando…`
      : `${users.join(", ")} estão digitando…`;
}

// ---------------------------------------------------------------------------
// Eventos Socket.IO — recepção
// ---------------------------------------------------------------------------

socket.on("history", ({ messages }) => {
  messages.forEach(({ username, text, sent_at }) => {
    appendMessage(username, text, sent_at, username === window.CURRENT_USER);
  });
});

socket.on("message", ({ username, text, sent_at }) => {
  appendMessage(username, text, sent_at, username === window.CURRENT_USER);
  typingUsers.delete(username);
  updateTypingIndicator();
});

socket.on("system", ({ text }) => {
  appendSystemMessage(text);
});

socket.on("typing", ({ username, typing }) => {
  if (typing) {
    typingUsers.add(username);
  } else {
    typingUsers.delete(username);
  }
  updateTypingIndicator();
});

// ---------------------------------------------------------------------------
// Envio
// ---------------------------------------------------------------------------

function sendMessage() {
  const text = messageInput.value.trim();
  if (!text) return;

  socket.emit("send_message", { text });
  messageInput.value = "";

  clearTimeout(typingTimer);
  socket.emit("typing", { typing: false });
}

sendBtn.addEventListener("click", sendMessage);

messageInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
});

// ---------------------------------------------------------------------------
// Sinalização de digitação
// ---------------------------------------------------------------------------

messageInput.addEventListener("input", () => {
  socket.emit("typing", { typing: true });
  clearTimeout(typingTimer);
  typingTimer = setTimeout(() => {
    socket.emit("typing", { typing: false });
  }, 2000);
});
