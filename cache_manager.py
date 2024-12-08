import redis
import logging
import json
import os
from datetime import datetime
from functools import wraps
import time

# Redis configuration
REDIS_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
CACHE_VERSION = "1.1"
MAX_RETRIES = 3
BASE_BACKOFF = 0.1
DRAWING_CACHE_TIMEOUT = 3600  # 1 hour
ROOM_CACHE_TIMEOUT = 86400   # 24 hours
USER_PRESENCE_TIMEOUT = 300  # 5 minutes
PREFETCH_THRESHOLD = 10      # Number of accesses before prefetching

# Initialize Redis connection pool with optimized settings
redis_pool = redis.ConnectionPool.from_url(
    REDIS_URL,
    max_connections=20,
    socket_timeout=2,
    socket_connect_timeout=2,
    retry_on_timeout=True,
    decode_responses=True,
    health_check_interval=30
)

redis_client = redis.Redis(connection_pool=redis_pool, decode_responses=True)

def get_cache_key(base_key, version=CACHE_VERSION):
    """Generate versioned cache key"""
    return f"{base_key}:v{version}"

def retry_with_backoff(func):
    """Enhanced retry decorator with exponential backoff and Redis-specific error handling"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        for attempt in range(MAX_RETRIES):
            try:
                return func(*args, **kwargs)
            except redis.ConnectionError as e:
                if attempt == MAX_RETRIES - 1:
                    logging.error(f"Redis connection failed after {MAX_RETRIES} attempts: {str(e)}")
                    raise
                backoff = BASE_BACKOFF * (2 ** attempt)
                logging.warning(f"Redis connection failed (attempt {attempt + 1}): {str(e)}. Retrying in {backoff}s")
                time.sleep(backoff)
            except redis.RedisError as e:
                logging.error(f"Redis operation error: {str(e)}")
                raise
            except Exception as e:
                logging.error(f"Unexpected cache error: {str(e)}")
                raise
    return wrapper

def log_cache_stats(func):
    """Log cache hit/miss statistics"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        start_time = time.time()
        result = func(*args, **kwargs)
        duration = time.time() - start_time
        
        # Log cache operation stats
        cache_status = "hit" if result is not None else "miss"
        logging.info(f"Cache {cache_status} - Key: {kwargs.get('cache_key')} - Duration: {duration:.3f}s")
        
        return result
    return wrapper


def cache_drawing(timeout=300):
    """Enhanced caching decorator with retry and monitoring"""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            room_id = kwargs.get('room_id')
            base_key = f"drawing_{room_id}"
            cache_key = get_cache_key(base_key)
            kwargs['cache_key'] = cache_key  # For logging
            
            @retry_with_backoff
            @log_cache_stats
            def get_cached_data():
                return redis_client.get(cache_key)
            
            @retry_with_backoff
            def set_cached_data(data):
                redis_client.set(cache_key, data, timeout=timeout)
                # Update access patterns for prefetching
                update_access_pattern(room_id)
            
            # Try to get cached data
            cached_data = get_cached_data()
            if cached_data is not None:
                return cached_data
            
            # Get fresh data
            data = f(*args, **kwargs)
            if data is not None:
                set_cached_data(data)
            return data
            
        return decorated_function
    return decorator

def update_access_pattern(room_id):
    """Track room access patterns for prefetching"""
    pattern_key = f"access_pattern:{room_id}"
    try:
        redis_client.set(
            pattern_key,
            {
                'last_access': datetime.utcnow().isoformat(),
                'access_count': redis_client.get(pattern_key, {}).get('access_count', 0) + 1
            },
            timeout=86400  # 24 hours
        )
    except Exception as e:
        logging.warning(f"Failed to update access pattern: {str(e)}")

def prefetch_room_data(room_id):
    """Prefetch room data based on access patterns"""
    pattern_key = f"access_pattern:{room_id}"
    try:
        pattern = redis_client.get(pattern_key)
        if pattern and pattern.get('access_count', 0) > PREFETCH_THRESHOLD:
            # Room is frequently accessed, prefetch related data
            logging.info(f"Prefetching data for frequently accessed room: {room_id}")
            
            # Prefetch drawing data
            from models import DrawingData # Assumed this import is available
            drawings = DrawingData.query.filter_by(room_id=room_id).all()
            if drawings:
                try:
                    drawing_data = [json.loads(d.data) for d in drawings]
                    cache_key = get_cache_key(f"drawing_data_{room_id}")
                    
                    @retry_with_backoff
                    def cache_drawings():
                        redis_client.setex(
                            cache_key,
                            DRAWING_CACHE_TIMEOUT,
                            json.dumps(drawing_data, separators=(',', ':'))
                        )
                    
                    cache_drawings()
                    logging.info(f"Successfully prefetched {len(drawing_data)} drawings for room {room_id}")
                except json.JSONDecodeError as e:
                    logging.error(f"Error parsing drawing data during prefetch: {e}")
    except Exception as e:
        logging.warning(f"Failed to prefetch room data: {str(e)}")

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
def get_room_state(room_id):
    """Retrieve cached room state"""
    try:
        cache_key = get_cache_key(f"room_state_{room_id}")
        data = redis_client.get(cache_key)
        if data:
            return json.loads(data)
    except Exception as e:
        logging.error(f"Failed to get room state: {str(e)}")
    return None

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
def invalidate_room_cache(room_id):
    """Invalidate all cached data for a room"""
    try:
        # Get all keys for the room
        room_pattern = get_cache_key(f"*_{room_id}")
        keys = redis_client.keys(room_pattern)
        
        if keys:
            redis_client.delete(*keys)
            logging.info(f"Invalidated cache for room {room_id}")
    except Exception as e:
        logging.error(f"Failed to invalidate room cache: {str(e)}")
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

@retry_with_backoff
def cache_cursor_position(room_id, user_id, position_data, timeout=2):
    """Cache cursor position with optimized connection handling"""
    if not check_redis_connection():
        logging.error("Redis connection is not available")
        return

    try:
        cursor_key = get_cache_key(f"cursor_{room_id}_{user_id}")
        with redis_client.pipeline(transaction=False) as pipe:
            try:
                # Set cursor position with expiration
                pipe.setex(cursor_key, timeout, json.dumps(position_data))
                pipe.execute()
                logging.debug(f"Successfully cached cursor position for user {user_id} in room {room_id}")
            except redis.RedisError as e:
                logging.error(f"Redis operation failed: {str(e)}")
            except Exception as e:
                logging.error(f"Unexpected error in cursor caching: {str(e)}")
    except Exception as e:
        logging.error(f"Failed to cache cursor position: {str(e)}")
    finally:
        try:
            # Safely release connection
            if hasattr(redis_client, 'connection') and redis_client.connection:
                redis_client.connection_pool.release(redis_client.connection)
        except Exception as e:
            logging.error(f"Error releasing Redis connection: {str(e)}")

@retry_with_backoff
def get_cursor_positions(room_id):
    """Get all active cursor positions in a room"""
    try:
        pattern = get_cache_key(f"cursor_{room_id}_*")
        cursor_keys = redis_client.keys(pattern)
        positions = {}
        
        for key in cursor_keys:
            user_id = key.split('_')[-1]
            data = redis_client.get(key)
            if data:
                positions[user_id] = json.loads(data)
        return positions
    except Exception as e:
        logging.error(f"Failed to get cursor positions: {str(e)}")
        return {}

def check_redis_connection():
    """Enhanced Redis connection health check with retry"""
    for attempt in range(3):
        try:
            if redis_client.ping():
                if attempt > 0:
                    logging.info("Redis connection restored")
                return True
        except redis.ConnectionError as e:
            if attempt == 2:
                logging.error(f"Redis connection failed after 3 attempts: {e}")
            else:
                logging.warning(f"Redis connection attempt {attempt + 1} failed: {e}")
                time.sleep(0.1 * (2 ** attempt))  # Exponential backoff
        except Exception as e:
            logging.error(f"Unexpected Redis error: {e}")
            break
    return False

# Export the redis client as cache
cache = redis_client