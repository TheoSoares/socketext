"""
SockeText — Frontend HTTP
Serve login + chat via Flask. O chat abre uma conexão TCP direta com o backend.
"""

from flask import Flask, request, render_template, redirect, url_for, session
from dotenv import load_dotenv
import os

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret")


@app.route("/", methods=["GET"])
def home():
    return render_template("login.html")


@app.route("/chat", methods=["GET"])
def chat():
    name = session.get("name")
    if not name:
        return redirect(url_for("home"))
    # Lista de servidores: "host:wsPort:httpPort,host:wsPort:httpPort"
    servers_raw = os.getenv("SOCKET_SERVERS", "127.0.0.1:9000:5000").split(",")
    servers = []
    for s in servers_raw:
        parts = s.strip().split(":")
        if len(parts) == 3:
            servers.append({"host": parts[0], "wsPort": int(parts[1]), "httpPort": int(parts[2])})
        elif len(parts) == 2:
            servers.append({"host": parts[0], "wsPort": int(parts[1]), "httpPort": 5000})
    return render_template("chat.html", servers=servers, name=name)


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
