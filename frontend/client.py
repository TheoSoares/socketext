from flask import Flask, request, render_template, redirect, url_for, session
from dotenv import load_dotenv
import os

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY')

# <-::- APP Configs -::->

@app.route('/', methods=['GET'])
def home() -> str:
    return render_template('login.html')

@app.route('/chat', methods=['GET'])
def chat():
    name = session.get('name')
    if not name:
         return redirect(url_for('home'))
    servers = os.getenv(
            'SOCKET_SERVERS',
            ''
    ).split(',')
    return render_template('chat.html', 
                        servers=servers,
                        name=name
                        )

@app.route('/login', methods=['GET', 'POST'])
def login() -> any:
    if request.method == 'POST':
        session['name'] = request.form['name']
        print(session['name'])
        return redirect(url_for('chat'))
    return redirect(url_for('home'))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=os.getenv('PORT'))
