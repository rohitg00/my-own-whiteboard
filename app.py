import os
from datetime import datetime
from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit, join_room
from cache_manager import check_redis_connection, cache
from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit, join_room
from flask_sqlalchemy import SQLAlchemy
import json
import redis
import time
from datetime import datetime

app = Flask(__name__)
app.config['SECRET_KEY'] = os.urandom(24)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///whiteboard.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Define SimpleCache as fallback
class SimpleCache:
    def __init__(self):
        self._cache = {}
        
    def get(self, key):
        return self._cache.get(key)
        
    def set(self, key, value, *args, **kwargs):
        self._cache[key] = value
        
    def setex(self, key, time, value):
        self._cache[key] = value
        
    def delete(self, key):
        self._cache.pop(key, None)
        
    def ping(self):
        return True

def init_redis_connection(max_retries=3, retry_delay=1):
    redis_url = os.getenv('REDIS_URL')
    
    if not redis_url:
        app.logger.warning("No Redis URL provided, using in-memory cache")
        return SimpleCache()
    
    try:
        # Ensure proper URL format with retries
        if not redis_url.startswith(('redis://', 'rediss://')):
            redis_url = f"redis://{redis_url}"
        
        # Configure Redis with proper options
        redis_client = redis.from_url(
            redis_url,
            decode_responses=True,
            socket_timeout=5,
            retry_on_timeout=True,
            health_check_interval=30
        )
        
        # Test connection
        redis_client.ping()
        app.logger.info("Successfully connected to Redis")
        return redis_client
        
    except redis.ConnectionError as e:
        app.logger.error(f"Redis connection error: {e}")
        return SimpleCache()
    except Exception as e:
        app.logger.error(f"Unexpected Redis error: {e}")
        return SimpleCache()

# Initialize cache with retry mechanism
cache = init_redis_connection()

def check_redis_health():
    try:
        if hasattr(cache, 'ping'):
            cache.ping()
            return True
        return False
    except:
        return False

# Add periodic health check
@app.before_request
def check_cache_health():
    if not check_redis_health():
        app.logger.warning("Redis health check failed, reinitializing connection")
        global cache
        cache = init_redis_connection()

# Initialize Flask-SocketIO
socketio = SocketIO(app)
db = SQLAlchemy(app)

# Room user count tracking
room_users = {}

# Import models after db initialization
import models

@app.route('/health')
def health_check():
    """Health check endpoint for k8s and monitoring"""
    redis_status = check_redis_connection()
    return jsonify({
        'status': 'healthy' if redis_status else 'degraded',
        'redis': 'connected' if redis_status else 'disconnected',
        'timestamp': datetime.utcnow().isoformat()
    }), 200 if redis_status else 503

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/room/<room_id>')
def room(room_id):
    # Create room if it doesn't exist
    room = models.Room.query.get(room_id)
    if not room:
        room = models.Room(id=room_id)
        db.session.add(room)
        db.session.commit()
    return render_template('room.html', room_id=room_id)

@app.route('/room/<room_id>/drawings')
def get_room_drawings(room_id):
    try:
        app.logger.info(f"Fetching drawings for room {room_id}")
        
        # Try to get from cache first
        cache_key = f"drawing_data_{room_id}"
        cached_data = cache.get(cache_key)
        
        if cached_data:
            app.logger.info("Retrieved drawings from cache")
            return {"drawings": json.loads(cached_data)}
        
        # If not in cache, get from database
        drawings = models.DrawingData.query.filter_by(room_id=room_id).all()
        drawing_data = []
        
        for drawing in drawings:
            try:
                path_obj = json.loads(drawing.data)
                drawing_data.append(path_obj)
            except json.JSONDecodeError as e:
                app.logger.error(f"Error parsing drawing data: {e}")
                continue
        
        # Update cache with fresh data
        cache.setex(cache_key, 3600, json.dumps(drawing_data))
        app.logger.info(f"Found {len(drawing_data)} drawings for room {room_id}")
        return {"drawings": drawing_data}
        
    except Exception as e:
        app.logger.error(f"Error retrieving drawings: {e}")
        return {"drawings": [], "error": str(e)}

@socketio.on('connect')
def handle_connect():
    app.logger.info(f"Client connected: {request.sid}")

@socketio.on('join')
def handle_join(data):
    room = data['room']
    join_room(room)
    
    # Update user count with proper room tracking
    if room not in room_users:
        room_users[room] = set()
    room_users[room].add(request.sid)
    
    # Broadcast user count to all clients in room
    user_count = len(room_users[room])
    socketio.emit('user_joined', {'count': user_count}, room=room)
    
    app.logger.info(f"Client {request.sid} joined room {room}, total users: {user_count}")

@socketio.on('draw')
def handle_draw(data):
    room = data['room']
    try:
        app.logger.info(f"Received draw event for room {room}")
        
        # Store in database with proper serialization
        path_data = json.dumps(data['path'], separators=(',', ':'))
        drawing = models.DrawingData(room_id=room, data=path_data)
        db.session.add(drawing)
        db.session.commit()
        
        # Update cache atomically
        cache_key = f"drawing_data_{room}"
        try:
            cached_data = cache.get(cache_key)
            drawing_list = json.loads(cached_data) if cached_data else []
            drawing_list.append(data['path'])
            cache.setex(cache_key, 3600, json.dumps(drawing_list, separators=(',', ':')))
            app.logger.info(f"Successfully updated cache for room {room}")
        except Exception as e:
            app.logger.error(f"Cache update failed: {e}")
        
        # Broadcast to room
        emit('draw_update', {
            'room': room,
            'path': data['path']
        }, room=room, include_self=False)
        
    except Exception as e:
        app.logger.error(f"Error handling draw event: {e}")
        db.session.rollback()

@socketio.on('disconnect')
def handle_disconnect():
    # Update user count for all rooms user was in
    for room in list(room_users.keys()):  # Use list to avoid runtime modification
        if request.sid in room_users[room]:
            room_users[room].remove(request.sid)
            user_count = len(room_users[room])
            socketio.emit('user_left', {'count': user_count}, room=room)
            app.logger.info(f"Client {request.sid} left room {room}, remaining users: {user_count}")
            
            # Clean up empty rooms
            if len(room_users[room]) == 0:
                del room_users[room]
                
    app.logger.info(f"Client disconnected: {request.sid}")

@socketio.on('undo')
def handle_undo(data):
    room = data['room']
    socketio.emit('undo_update', {
        'room': room,
        'objectData': data.get('objectData')
    }, room=room, skip_sid=request.sid)

@socketio.on('redo')
def handle_redo(data):
    room = data['room']
    socketio.emit('redo_update', {
        'room': room,
        'objectData': data.get('objectData')
    }, room=room, skip_sid=request.sid)

@socketio.on('clear')
@socketio.on('cursor_move')
def handle_cursor_move(data):
    room = data['room']
    emit('cursor_update', {
        'room': room,
        'userName': data['userName'],
        'x': data['x'],
        'y': data['y']
    }, room=room, include_self=False)

def handle_clear(data):
    room = data['room']
    try:
        # Clear cached drawing data
        cache_key = f"drawing_data_{room}"
        cache.delete(cache_key)
        
        # Clear drawings from database
        models.DrawingData.query.filter_by(room_id=room).delete()
        db.session.commit()
        
        socketio.emit('clear_board', room=room, skip_sid=request.sid)
    except Exception as e:
        app.logger.error(f"Error clearing drawings: {str(e)}")
        db.session.rollback()
        socketio.emit('error', {'message': 'Failed to clear drawings'}, room=request.sid)


# Start the server
if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)