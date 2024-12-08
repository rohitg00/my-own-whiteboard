import redis
import logging
import json
import os
import time
from datetime import datetime, timedelta
from functools import wraps
from cache_circuit_breaker import CircuitBreaker

# Configure logging
logging.basicConfig(level=logging.INFO)

# Constants
MAX_RETRIES = 3
BASE_BACKOFF = 0.1
USER_PRESENCE_TIMEOUT = 30  # seconds
DRAWING_CACHE_TIMEOUT = 3600  # 1 hour
ROOM_CACHE_TIMEOUT = 86400   # 24 hours
PREFETCH_THRESHOLD = 10      # Number of accesses before prefetching

# Configure Redis connection
REDIS_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
redis_client = redis.from_url(REDIS_URL)

# Initialize circuit breaker
cache_breaker = CircuitBreaker(failure_threshold=5, reset_timeout=60)

def get_cache_key(key):
    """Generate a namespaced cache key"""
    return f"whiteboard:{key}"

def retry_with_backoff(func):
    """Retry function with exponential backoff"""
    def wrapper(*args, **kwargs):
        max_attempts = 3
        attempt = 0
        backoff = 1
        
        while attempt < max_attempts:
            try:
                return func(*args, **kwargs)
            except Exception as e:
                attempt += 1
                if attempt == max_attempts:
                    raise e
                sleep_time = backoff * 2 ** attempt
                logging.warning(f"Retry attempt {attempt} for {func.__name__}, sleeping for {sleep_time}s")
                time.sleep(sleep_time)
        
    return wrapper

@retry_with_backoff
def monitor_redis_health():
    """Monitor Redis connection health and log status"""
    try:
        if check_redis_connection():
            logging.info("Redis connection is healthy")
            # Get cache stats
            info = redis_client.info()
            logging.info(f"Connected clients: {info.get('connected_clients', 'N/A')}")
            logging.info(f"Used memory: {info.get('used_memory_human', 'N/A')}")
            return True
        else:
            logging.error("Redis connection is not healthy")
            return False
    except Exception as e:
        logging.error(f"Error monitoring Redis health: {str(e)}")
        return False

@retry_with_backoff
def check_redis_connection():
    """Check Redis connection health"""
    try:
        redis_client.ping()
        return True
    except Exception as e:
        logging.error(f"Redis connection error: {e}")
        return False

@retry_with_backoff
def cache_room_state(room_id, state_data, timeout=ROOM_CACHE_TIMEOUT):
    """Cache room state including viewport and active users"""
    try:
        cache_key = get_cache_key(f"room_state_{room_id}")
        redis_client.setex(
            cache_key,
            timeout,
            json.dumps(state_data, separators=(',', ':'))
        )
        logging.info(f"Cached room state for room {room_id}")
    except Exception as e:
        logging.error(f"Failed to cache room state: {str(e)}")

@retry_with_backoff
def track_user_presence(room_id, user_id, user_data):
    """Track user presence in a room with cursor position caching"""
    try:
        presence_key = get_cache_key(f"presence_{room_id}")
        user_key = str(user_id)  # Ensure key is string
        
        # Add timestamp to user data for cleanup
        user_data['last_seen'] = datetime.utcnow().isoformat()
        
        # Serialize user data
        serialized_data = json.dumps(user_data)
        
        # Update user data in room with pipeline for atomicity
        pipe = redis_client.pipeline()
        pipe.hset(presence_key, user_key, serialized_data)
        pipe.expire(presence_key, USER_PRESENCE_TIMEOUT)
        pipe.execute()
        
        logging.info(f"Updated presence for user {user_id} in room {room_id}")
        
        # Cleanup disconnected users periodically
        cleanup_disconnected_users(room_id)
    except Exception as e:
        logging.error(f"Failed to track user presence: {str(e)}")

@retry_with_backoff
def get_active_users(room_id):
    """Get all active users in a room"""
    try:
        presence_key = get_cache_key(f"presence_{room_id}")
        user_data = redis_client.hgetall(presence_key)
        # Decode bytes to string if needed and parse JSON
        return {
            k.decode('utf-8') if isinstance(k, bytes) else k: 
            json.loads(v.decode('utf-8') if isinstance(v, bytes) else v)
            for k, v in user_data.items()
        }
    except Exception as e:
        logging.error(f"Failed to get active users: {str(e)}")
        return {}

@retry_with_backoff
def prefetch_room_data(room_id):
    """Prefetch room data based on access patterns"""
    pattern_key = get_cache_key(f"access_pattern:{room_id}")
    try:
        pattern = redis_client.get(pattern_key)
        if pattern and pattern.get('access_count', 0) > PREFETCH_THRESHOLD:
            # Room is frequently accessed, prefetch related data
            logging.info(f"Prefetching data for frequently accessed room: {room_id}")
            
            # Prefetch drawing data
            from models import DrawingData
            drawings = DrawingData.query.filter_by(room_id=room_id).all()
            if drawings:
                try:
                    drawing_data = [json.loads(d.data) for d in drawings]
                    cache_key = get_cache_key(f"drawing_data_{room_id}")
                    redis_client.setex(
                        cache_key,
                        DRAWING_CACHE_TIMEOUT,
                        json.dumps(drawing_data, separators=(',', ':'))
                    )
                    logging.info(f"Successfully prefetched {len(drawing_data)} drawings for room {room_id}")
                except json.JSONDecodeError as e:
                    logging.error(f"Error parsing drawing data during prefetch: {e}")
    except Exception as e:
        logging.warning(f"Failed to prefetch room data: {str(e)}")

@retry_with_backoff
def cache_cursor_position(room_id, user_id, position_data, timeout=5):
    """Cache single cursor position with debouncing"""
    try:
        cursor_key = get_cache_key(f"cursor_{room_id}_{user_id}")
        # Add timestamp for cursor expiration
        position_data['timestamp'] = datetime.utcnow().isoformat()
        redis_client.setex(cursor_key, timeout, json.dumps(position_data))
    except Exception as e:
        logging.error(f"Failed to cache cursor position: {str(e)}")

@retry_with_backoff
def cleanup_disconnected_users(room_id):
    """Remove users who haven't updated their presence recently"""
    try:
        presence_key = get_cache_key(f"presence_{room_id}")
        now = datetime.utcnow()
        users = get_active_users(room_id)
        
        pipe = redis_client.pipeline()
        for user_id, data in users.items():
            last_seen = datetime.fromisoformat(data.get('last_seen', '2000-01-01'))
            if (now - last_seen).total_seconds() > USER_PRESENCE_TIMEOUT:
                pipe.hdel(presence_key, str(user_id))
        pipe.execute()
    except Exception as e:
        logging.error(f"Failed to cleanup disconnected users: {str(e)}")

# Export the redis client as cache
cache = redis_client