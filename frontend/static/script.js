/**
 * script.js — Lógica do cliente web do SockeText.
 *
 * Conecta ao frontend Flask via Socket.IO e gerencia:
 *   - Renderização de mensagens e histórico
 *   - Indicador de digitação
 *   - Notificações de sistema
 *   - Envio de mensagens e sinalização de digitação
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
let isTyping = false;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function escapeHtml(str) {
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function scrollToBottom() {
  messagesList.scrollTop = messagesList.scrollHeight;
}

// ---------------------------------------------------------------------------
// Renderização
// ---------------------------------------------------------------------------

/**
 * Cria e insere uma bolha de mensagem na lista.
 *
 * @param {string} sender
 * @param {string} text
 * @param {string} [time]    - Horário formatado (HH:MM).
 * @param {boolean} [isOwn]  - True se é mensagem do próprio usuário.
 */
function appendMessage(sender, text, time = "", isOwn = false) {
  const displayTime = time || new Date().toLocaleTimeString("pt-BR", {
    hour: "2-digit",
    minute: "2-digit",
  });

  const item = document.createElement("div");
  item.classList.add("msg", isOwn ? "msg--own" : "msg--other");

  item.innerHTML = `
    <div class="msg__header">
      <span class="msg__user">${escapeHtml(sender)}</span>
      <span class="msg__time">${displayTime}</span>
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

// ---------------------------------------------------------------------------
// Indicador de digitação
// ---------------------------------------------------------------------------

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
  messages.forEach(({ sender, text, time }) => {
    appendMessage(sender, text, time, sender === window.CURRENT_USER);
  });
});

socket.on("message", ({ sender, text, time }) => {
  appendMessage(sender, text, time, sender === window.CURRENT_USER);
  // Remove do indicador de digitação caso estivesse lá
  typingUsers.delete(sender);
  updateTypingIndicator();
});

socket.on("system", ({ text }) => {
  appendSystemMessage(text);
});

socket.on("typing", ({ sender }) => {
  typingUsers.add(sender);
  updateTypingIndicator();
});

socket.on("stop_typing", ({ sender }) => {
  typingUsers.delete(sender);
  updateTypingIndicator();
});

// ---------------------------------------------------------------------------
// Envio
// ---------------------------------------------------------------------------

function sendMessage() {
  const text = messageInput.value.trim();
  if (!text) return;

  // Cancela sinalização de digitação antes de enviar
  if (isTyping) {
    clearTimeout(typingTimer);
    typingTimer = null;
    isTyping = false;
    socket.emit("stop_typing");
  }

  socket.emit("send_message", { text });
  messageInput.value = "";
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
  if (!isTyping) {
    isTyping = true;
    socket.emit("typing");
  }

  clearTimeout(typingTimer);
  typingTimer = setTimeout(() => {
    isTyping = false;
    socket.emit("stop_typing");
  }, 2000);
});
