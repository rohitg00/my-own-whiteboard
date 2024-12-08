from flask import Flask, render_template, request
from flask_socketio import SocketIO, join_room
from flask_sqlalchemy import SQLAlchemy
from flask_caching import Cache
import os
import logging
import time
import json

# Configure logging
logging.basicConfig(level=logging.DEBUG)

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///whiteboard.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Redis Cache Configuration
redis_url = os.environ.get('REDIS_URL')
cache_config = {
    'CACHE_DEFAULT_TIMEOUT': 300
}

if redis_url:
    try:
        # Parse Redis URL to extract authentication
        from urllib.parse import urlparse, parse_qs
        parsed_url = urlparse(redis_url)
        
        # Ensure URL has proper format and authentication
        if not redis_url.startswith(('redis://', 'rediss://')):
            redis_url = f"redis://{redis_url}"
        
        cache_config.update({
            'CACHE_TYPE': 'redis',
            'CACHE_REDIS_URL': redis_url
        })
        
        # Initialize cache
        app.config.update(cache_config)
        cache = Cache(app)
        
        # Test connection with retry
        for attempt in range(3):
            try:
                cache.set('test_key', 'test_value')
                test_value = cache.get('test_key')
                if test_value == 'test_value':
                    app.logger.info('Redis cache initialized successfully')
                    break
            except Exception as e:
                app.logger.error(f'Redis connection attempt {attempt + 1} failed: {str(e)}')
                if attempt < 2:  # Don't sleep on last attempt
                    time.sleep(1)
        else:
            raise Exception("Failed to connect to Redis after 3 attempts")
            
    except Exception as e:
        app.logger.error(f'Redis initialization failed: {str(e)}')
        app.logger.warning('Falling back to SimpleCache')
        cache_config['CACHE_TYPE'] = 'simple'
        app.config.update(cache_config)
        cache = Cache(app)
else:
    app.logger.warning('Redis URL not found, falling back to SimpleCache')
    cache_config['CACHE_TYPE'] = 'simple'
    app.config.update(cache_config)
    cache = Cache(app)

# Initialize extensions
socketio = SocketIO(app)
db = SQLAlchemy(app)

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
def get_room_drawings(room_id):
    try:
        app.logger.info(f"Fetching drawings for room {room_id}")
        drawings = models.DrawingData.query.filter_by(room_id=room_id).all()
        drawing_data = []
        
        for drawing in drawings:
            try:
                path_obj = json.loads(drawing.data)
                drawing_data.append(path_obj)
            except json.JSONDecodeError as e:
                app.logger.error(f"Error parsing drawing data: {e}")
                continue
        
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
    app.logger.info(f"Client {request.sid} joined room {room}")

@socketio.on('draw')
def handle_draw(data):
    room = data['room']
    try:
        # Ensure path data is properly serialized
        path_data = json.dumps(data['path'], separators=(',', ':'))
        
        # Store in database first
        drawing = models.DrawingData(room_id=room, data=path_data)
        db.session.add(drawing)
        db.session.commit()
        app.logger.info(f"Drawing saved to database for room {room}")
        
        # Update cache with parsed data
        cache_key = f"drawing_data_{room}"
        cached_data = cache.get(cache_key) or []
        cached_data.append(data['path'])
        cache.set(cache_key, cached_data)
        
        # Broadcast to room
        socketio.emit('draw_update', data, room=room, skip_sid=request.sid)
        
    except Exception as e:
        app.logger.error(f"Error saving drawing: {str(e)}")
        db.session.rollback()
        socketio.emit('error', {'message': 'Failed to save drawing'}, room=request.sid)

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
