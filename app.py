import os
from datetime import datetime
from flask import Flask, render_template, request, jsonify
from flask_socketio import emit, join_room
import json
import logging

from extensions import db, socketio
from cache_manager import (
    cache, check_redis_connection, cache_room_state,
    track_user_presence, get_active_users, prefetch_room_data,
    cache_cursor_position  # Add cursor position caching
)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['SECRET_KEY'] = os.urandom(24)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///whiteboard.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Initialize extensions
db.init_app(app)
socketio.init_app(app)

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
        logger.info(f"Fetching drawings for room {room_id}")
        
        # Try to get from cache first
        cache_key = f"drawing_data_{room_id}"
        cached_data = cache.get(cache_key)
        
        if cached_data:
            logger.info("Retrieved drawings from cache")
            return {"drawings": json.loads(cached_data)}
        
        # If not in cache, get from database
        drawings = models.DrawingData.query.filter_by(room_id=room_id).all()
        drawing_data = []
        
        for drawing in drawings:
            try:
                path_obj = json.loads(drawing.data)
                drawing_data.append(path_obj)
            except json.JSONDecodeError as e:
                logger.error(f"Error parsing drawing data: {e}")
                continue
        
        # Update cache with fresh data
        cache.setex(cache_key, 3600, json.dumps(drawing_data))
        logger.info(f"Found {len(drawing_data)} drawings for room {room_id}")
        return {"drawings": drawing_data}
        
    except Exception as e:
        logger.error(f"Error retrieving drawings: {e}")
        return {"drawings": [], "error": str(e)}

# Room user count tracking
room_users = {}

@socketio.on('connect')
def handle_connect():
    logger.info(f"Client connected: {request.sid}")

@socketio.on('join')
def handle_join(data):
    room = data['room']
    join_room(room)
    
    try:
        # Update user count with proper room tracking
        if room not in room_users:
            room_users[room] = set()
        room_users[room].add(request.sid)
        user_count = len(room_users[room])
        
        # Track user presence in Redis
        user_data = {
            'sid': request.sid,
            'joined_at': datetime.utcnow().isoformat(),
            'user_name': data.get('userName', 'Anonymous')
        }
        track_user_presence(room, request.sid, user_data)
        
        # Cache room state
        cache_room_state(room, {
            'user_count': user_count,
            'last_update': datetime.utcnow().isoformat()
        })
        
        # Prefetch room data for frequently accessed rooms
        prefetch_room_data(room)
        
        # Broadcast user count to all clients in room
        socketio.emit('user_joined', {
            'count': user_count,
            'users': get_active_users(room)
        }, room=room)
        
        logger.info(f"Client {request.sid} joined room {room}, total users: {user_count}")
        
    except Exception as e:
        logger.error(f"Error in handle_join: {str(e)}")
        # Ensure basic functionality even if caching fails
        socketio.emit('user_joined', {'count': len(room_users.get(room, set()))}, room=room)

@socketio.on('draw')
def handle_draw(data):
    room = data['room']
    try:
        logger.info(f"Received draw event for room {room}")
        
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
            logger.info(f"Successfully updated cache for room {room}")
        except Exception as e:
            logger.error(f"Cache update failed: {e}")
        
        # Broadcast to room
        emit('draw_update', {
            'room': room,
            'path': data['path']
        }, room=room, include_self=False)
        
    except Exception as e:
        logger.error(f"Error handling draw event: {e}")
        db.session.rollback()

@socketio.on('disconnect')
def handle_disconnect():
    # Update user count for all rooms user was in
    for room in list(room_users.keys()):  # Use list to avoid runtime modification
        if request.sid in room_users[room]:
            room_users[room].remove(request.sid)
            user_count = len(room_users[room])
            socketio.emit('user_left', {'count': user_count}, room=room)
            logger.info(f"Client {request.sid} left room {room}, remaining users: {user_count}")
            
            # Clean up empty rooms
            if len(room_users[room]) == 0:
                del room_users[room]
                
    logger.info(f"Client disconnected: {request.sid}")

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

@socketio.on('cursor_move')
def handle_cursor_move(data):
    room = data['room']
    try:
        # Cache cursor position
        cursor_data = {
            'userName': data['userName'],
            'x': data['x'],
            'y': data['y'],
            'timestamp': datetime.utcnow().isoformat()
        }
        cache_cursor_position(room, request.sid, cursor_data)
        
        # Broadcast to room
        emit('cursor_update', {
            'room': room,
            'userName': data['userName'],
            'x': data['x'],
            'y': data['y']
        }, room=room, include_self=False)
    except Exception as e:
        logger.error(f"Error handling cursor move: {str(e)}")


@socketio.on('viewport_update')
def handle_viewport_update(data):
    """Handle viewport updates and cache room state"""
    room = data['room']
    try:
        from cache_manager import cache_room_state
        cache_room_state(room, {
            'viewport': data['viewport'],
            'last_update': datetime.utcnow().isoformat()
        })
        emit('viewport_update', data, room=room, skip_sid=request.sid)
    except Exception as e:
        logger.error(f"Error caching viewport state: {e}")

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
        logger.error(f"Error clearing drawings: {str(e)}")
        db.session.rollback()
        socketio.emit('error', {'message': 'Failed to clear drawings'}, room=request.sid)

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)