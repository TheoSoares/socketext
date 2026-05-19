/**
 * script.js — Lógica do cliente web do SockeText.
 *
 * Conecta diretamente ao servidor backend via Socket.IO.
 * Implementa tryConnect: percorre a lista SERVERS em ordem e reconecta
 * automaticamente ao cair, igual ao comportamento do código original.
 */

"use strict";

// ---------------------------------------------------------------------------
// Referências DOM
// ---------------------------------------------------------------------------

const messagesList = document.getElementById("messages");
const messageInput = document.getElementById("message-input");
const sendBtn      = document.getElementById("send-btn");
const typingEl     = document.getElementById("typing-indicator");
const statusDot    = document.getElementById("statusDot");
const statusLabel  = document.getElementById("statusLabel");

// ---------------------------------------------------------------------------
// Estado local
// ---------------------------------------------------------------------------

const typingUsers = new Set();
let typingTimer   = null;
let isTyping      = false;
var socket        = null;

// ---------------------------------------------------------------------------
// Status de conexão
// ---------------------------------------------------------------------------

function setStatus(state) {
  const colors = { online: "#3fcf8e", offline: "#e24b4a", connecting: "#EF9F27" };
  const labels = { online: "ao vivo", offline: "desconectado", connecting: "conectando..." };
  statusDot.style.background = colors[state] || colors.connecting;
  statusLabel.textContent    = labels[state]  || state;
}

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

function appendMessage(sender, text, time, isOwn) {
  const displayTime = time || new Date().toLocaleTimeString("pt-BR", {
    hour: "2-digit", minute: "2-digit",
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

function appendSystemMessage(text) {
  const item = document.createElement("div");
  item.classList.add("msg", "msg--system");
  item.innerHTML = `<div class="msg__bubble">${escapeHtml(text)}</div>`;
  messagesList.appendChild(item);
  scrollToBottom();
}

function clearMessages() {
  messagesList.replaceChildren();
}

// ---------------------------------------------------------------------------
// Indicador de digitação
// ---------------------------------------------------------------------------

function updateTypingIndicator() {
  const users = [...typingUsers].filter(u => u !== NAME);
  if (users.length === 0) {
    typingEl.hidden = true;
    typingEl.textContent = "";
    return;
  }
  typingEl.hidden = false;
  typingEl.textContent = users.length === 1
    ? `${users[0]} está digitando…`
    : `${users.join(", ")} estão digitando…`;
}

// ---------------------------------------------------------------------------
// Envio
// ---------------------------------------------------------------------------

function sendMessage() {
  const text = messageInput.value.trim();
  if (!text) return;

  if (isTyping) {
    clearTimeout(typingTimer);
    typingTimer = null;
    isTyping = false;
    if (socket && socket.connected) {
      socket.emit("message", { type: "stop_typing", sender: NAME });
    }
  }

  if (socket && socket.connected) {
    socket.emit("message", { type: "message", sender: NAME, text });
    appendMessage(NAME, text, null, true);
    messageInput.value = "";
  }
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
  if (!socket || !socket.connected) return;

  if (!isTyping) {
    isTyping = true;
    socket.emit("message", { type: "typing", sender: NAME });
  }

  clearTimeout(typingTimer);
  typingTimer = setTimeout(() => {
    isTyping = false;
    if (socket && socket.connected) {
      socket.emit("message", { type: "stop_typing", sender: NAME });
    }
  }, 2000);
});

// ---------------------------------------------------------------------------
// tryConnect — percorre SERVERS em ordem, reconecta ao cair
// ---------------------------------------------------------------------------

function tryConnect(index) {
  index = index === undefined ? 0 : index;

  if (index >= SERVERS.length) {
    console.warn("Nenhum servidor disponível. Tentando novamente em 2s...");
    setStatus("offline");
    setTimeout(() => tryConnect(0), 2000);
    return;
  }

  const server = SERVERS[index];
  setStatus("connecting");

  socket = io(server, {
    timeout: 3000,
    reconnection: false,
  });

  socket.on("connect", () => {
    console.log("Conectado:", server);
    setStatus("online");
  });

  socket.on("connect_error", () => {
    console.warn("Falhou:", server);
    socket.disconnect();
    tryConnect(index + 1);
  });

  // Histórico recebido ao conectar — limpa tela e recarrega
  socket.on("history_load", (messages) => {
    clearMessages();
    messages.forEach(({ sender, text, time }) => {
      appendMessage(sender, text, time, sender === NAME);
    });
  });

  // Mensagem de outro usuário
  socket.on("message", (data) => {
    if (data.type === "typing") {
      typingUsers.add(data.sender);
      updateTypingIndicator();
    } else if (data.type === "stop_typing") {
      typingUsers.delete(data.sender);
      updateTypingIndicator();
    } else if (data.type === "message" && data.sender !== NAME) {
      appendMessage(data.sender, data.text, data.time || null, false);
      typingUsers.delete(data.sender);
      updateTypingIndicator();
    }
  });

  socket.on("disconnect", (reason) => {
    console.warn("Desconectado:", reason);
    setStatus("offline");
    typingUsers.clear();
    updateTypingIndicator();
    // Tenta reconectar do início da lista
    tryConnect(0);
  });
}

// Inicia
tryConnect(0);

