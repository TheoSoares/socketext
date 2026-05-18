from flask import Flask
from flask_socketio import SocketIO, emit
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text
from dotenv import load_dotenv
import os

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY')
socketio = SocketIO(app, cors_allowed_origins='*', message_queue=os.environ.get('REDIS_URL'))

DEBUG = os.getenv("DEBUG", "false").lower() == "true"
DEBUG = False
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('EXTERNAL_DATABASE_URL') if DEBUG else os.environ.get('INTERNAL_DATABASE_URL')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

#<-::- SocketIO Configs -::->

@socketio.on('connect')
def get_history(auth=None) -> None:
    print(f'New connection -- Sending history...')
    sql_command = text('SELECT username, sent_at, message FROM messages ORDER BY sent_at')
    result = db.session.execute(sql_command)
    new_result = [{'sender': row[0], 'time': row[1].strftime('%H:%M'), 'text': row[2]} for row in list(result)]
    print(f'Data: {new_result}')
    emit('history_load', new_result)

@socketio.on('message')
def handle_message(data: str) -> None:
    print(f'Received Data: {data}')
    if data['type'] == 'typing' or data['type'] == 'stop_typing':
        emit('message', data, include_self=False, broadcast=True)
        return None
    
    sql_command = text('INSERT INTO messages (username, message) VALUES (:username, :message)')
    db.session.execute(sql_command, {'username': data['sender'], 'message': data['text']})
    db.session.commit()

    emit('message', data, include_self=False, broadcast=True)

    return None

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=os.getenv('PORT'))
