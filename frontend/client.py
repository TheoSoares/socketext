"""
client.py — Frontend do SockeText.

Responsabilidades:
  - Servir a interface web (HTML/JS/CSS) via Flask.
  - Gerenciar sessão de login do usuário.
  - Passar ao template a lista de servidores backend e o nome do usuário;
    o JS do browser conecta diretamente ao backend via Socket.IO e
    implementa o tryConnect com fallback automático entre servidores.
"""

import os
from flask import Flask, request, render_template, redirect, url_for, session
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY")

# Lista de URLs dos servidores backend, separadas por vírgula.
# O JS receberá esta lista e tentará cada servidor em ordem (tryConnect).
SOCKET_SERVERS: list[str] = [
    s.strip()
    for s in os.getenv("SOCKET_SERVERS", "").split(",")
    if s.strip()
]


@app.route("/", methods=["GET"])
def home():
    return render_template("login.html")


@app.route("/chat", methods=["GET"])
def chat():
    name = session.get("name")
    if not name:
        return redirect(url_for("home"))
    return render_template("chat.html", servers=SOCKET_SERVERS, name=name)


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if name:
            session["name"] = name
        return redirect(url_for("chat"))
    return redirect(url_for("home"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    app.run(host="0.0.0.0", port=port)

