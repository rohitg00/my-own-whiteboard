from flask import Flask, render_template, request
from flask_socketio import SocketIO, join_room
from flask_sqlalchemy import SQLAlchemy
from flask_caching import Cache
import os
import logging

# Configure logging
logging.basicConfig(level=logging.DEBUG)

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///whiteboard.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Redis Cache Configuration
app.config['CACHE_TYPE'] = 'redis'
redis_url = os.environ.get('REDIS_URL')
if redis_url and not any(redis_url.startswith(prefix) for prefix in ['redis://', 'rediss://', 'unix://']):
    redis_url = f"redis://{redis_url}"
app.config['CACHE_REDIS_URL'] = redis_url
app.config['CACHE_DEFAULT_TIMEOUT'] = 300

# Initialize extensions
socketio = SocketIO(app)
db = SQLAlchemy(app)
cache = Cache(app)

# Import models after db initialization to avoid circular imports
import models

# Create database tables
with app.app_context():
    db.create_all()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/room/<room_id>')
@cache.memoize(timeout=300)
def room(room_id):
    # Check if room exists, if not create it
    room = models.Room.query.get(room_id)
    if not room:
        room = models.Room(id=room_id)
        db.session.add(room)
        db.session.commit()
    return render_template('room.html', room_id=room_id)

@app.route('/room/<room_id>/drawings')
@cache.memoize(timeout=300)
def get_room_drawings(room_id):
    cache_key = f"drawing_data_{room_id}"
    cached_data = cache.get(cache_key)
    if cached_data is not None:
        return {"drawings": cached_data}
    
    # If not in cache, get from database
    drawings = models.DrawingData.query.filter_by(room_id=room_id).all()
    drawing_data = [eval(d.data) for d in drawings]  # Convert string back to dict
    cache.set(cache_key, drawing_data, timeout=300)
    return {"drawings": drawing_data}

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
    # Store drawing data in database
    drawing = models.DrawingData(room_id=room, data=str(data['path']))
    db.session.add(drawing)
    db.session.commit()
    
    # Cache the latest drawing data
    cache_key = f"drawing_data_{room}"
    cached_data = cache.get(cache_key) or []
    cached_data.append(data['path'])
    cache.set(cache_key, cached_data, timeout=300)
    
    socketio.emit('draw_update', data, room=room, skip_sid=request.sid)

@socketio.on('clear')
def handle_clear(data):
    room = data['room']
    # Clear cached drawing data
    cache_key = f"drawing_data_{room}"
    cache.delete(cache_key)
    
    # Clear drawings from database
    models.DrawingData.query.filter_by(room_id=room).delete()
    db.session.commit()
    
    socketio.emit('clear_board', room=room, skip_sid=request.sid)

@socketio.on('disconnect')
def handle_disconnect():
    app.logger.info(f"Client disconnected: {request.sid}")
