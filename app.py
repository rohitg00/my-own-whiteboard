import os
from flask import Flask, render_template, request, session
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO, join_room, leave_room
from flask_caching import Cache
from sqlalchemy.orm import DeclarativeBase
import logging

logging.basicConfig(level=logging.DEBUG)

class Base(DeclarativeBase):
    pass

db = SQLAlchemy(model_class=Base)
socketio = SocketIO()
cache = Cache(config={
    'CACHE_TYPE': 'redis',
    'CACHE_REDIS_URL': os.environ.get('REDIS_URL', 'redis://localhost:6379/0'),
    'CACHE_DEFAULT_TIMEOUT': 300
})

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or "a secret key"
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL", "sqlite:///whiteboard.db")
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_recycle": 300,
    "pool_pre_ping": True,
}

db.init_app(app)
socketio.init_app(app, cors_allowed_origins="*")
cache.init_app(app)

with app.app_context():
    import models
    db.create_all()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/room/<room_id>')
def room(room_id):
    return render_template('room.html', room_id=room_id)

@socketio.on('connect')
def handle_connect():
    app.logger.info(f"Client connected: {request.sid}")

@socketio.on('join')
def handle_join(data):
    room = data['room']
    join_room(room)
    app.logger.info(f"Client {request.sid} joined room {room}")

@socketio.on('draw')
def handle_draw(data):
    room = data['room']
    socketio.emit('draw_update', data, room=room, skip_sid=request.sid)

@socketio.on('clear')
def handle_clear(data):
    room = data['room']
    socketio.emit('clear_board', room=room, skip_sid=request.sid)

@socketio.on('disconnect')
def handle_disconnect():
    app.logger.info(f"Client disconnected: {request.sid}")
