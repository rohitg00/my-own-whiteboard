import os
from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit, join_room
from flask_sqlalchemy import SQLAlchemy
import json
import redis
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
    """Initialize Redis connection with retries"""
    redis_url = os.getenv('REDIS_URL')
    
    if not redis_url:
        app.logger.warning("No Redis URL provided, using in-memory cache")
        return SimpleCache()
        
    for attempt in range(max_retries):
        try:
            # Ensure proper URL format
            if not redis_url.startswith(('redis://', 'rediss://')):
                redis_url = f"redis://{redis_url}"
            
            # Initialize connection
            redis_client = redis.from_url(redis_url, decode_responses=True)
            redis_client.ping()  # Test connection
            
            app.logger.info(f"Successfully connected to Redis (attempt {attempt + 1}/{max_retries})")
            return redis_client
            
        except redis.ConnectionError as e:
            app.logger.error(f"Redis connection error (attempt {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
            else:
                app.logger.warning("Max retries reached, falling back to in-memory cache")
                return SimpleCache()
                
        except Exception as e:
            app.logger.error(f"Unexpected Redis error: {e}")
            return SimpleCache()

# Initialize cache with retry mechanism
cache = init_redis_connection()

# Monitor cache health
def check_cache_health():
    """Periodic cache health check"""
    try:
        if isinstance(cache, redis.Redis):
            cache.ping()
            return True
    except:
        app.logger.error("Cache health check failed")
        return False
    return True

# Initialize Flask-SocketIO
socketio = SocketIO(app)
db = SQLAlchemy(app)

# Room user count tracking
room_users = {}

# Import models after db initialization to avoid circular imports
import models

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
        cache.setex(cache_key, 3600, json.dumps(drawing_data))  # Cache for 1 hour
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
    
    # Update user count
    if room not in room_users:
        room_users[room] = set()
    room_users[room].add(request.sid)
    
    # Emit updated count to all users in room
    user_count = len(room_users[room])
    socketio.emit('user_joined', {'count': user_count}, room=room)
    
    app.logger.info(f"Client {request.sid} joined room {room}, total users: {user_count}")

@socketio.on('draw')
def handle_draw(data):
    room = data['room']
    try:
        # Serialize path data with proper encoding
        path_data = json.dumps(data['path'])
        
        # Store in database
        drawing = models.DrawingData(room_id=room, data=path_data)
        db.session.add(drawing)
        db.session.commit()
        
        # Update cache
        cache_key = f"drawing_data_{room}"
        cached_data = cache.get(cache_key)
        if cached_data:
            drawing_list = json.loads(cached_data)
            drawing_list.append(data['path'])
            cache.setex(cache_key, 3600, json.dumps(drawing_list))
        else:
            cache.setex(cache_key, 3600, json.dumps([data['path']]))
        
        # Broadcast to room with proper data structure
        socketio.emit('draw_update', {
            'room': room,
            'path': data['path']
        }, room=room, skip_sid=request.sid)
        
    except Exception as e:
        app.logger.error(f"Error saving drawing: {str(e)}")
        db.session.rollback()

@socketio.on('undo')
def handle_undo(data):
    room = data['room']
    socketio.emit('undo_update', data, room=room, skip_sid=request.sid)

@socketio.on('redo')
def handle_redo(data):
    room = data['room']
    socketio.emit('redo_update', data, room=room, skip_sid=request.sid)

@socketio.on('clear')
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

@socketio.on('disconnect')
def handle_disconnect():
    # Update user count for all rooms user was in
    for room in room_users:
        if request.sid in room_users[room]:
            room_users[room].remove(request.sid)
            user_count = len(room_users[room])
            socketio.emit('user_left', {'count': user_count}, room=room)
            app.logger.info(f"Client {request.sid} left room {room}, remaining users: {user_count}")
    
    app.logger.info(f"Client disconnected: {request.sid}")