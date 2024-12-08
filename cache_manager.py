from app import app
from functools import wraps
import time
import logging
import json
import redis
from datetime import datetime
import os

# Initialize Redis connection pool
REDIS_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
redis_pool = redis.ConnectionPool.from_url(
    REDIS_URL,
    max_connections=10,
    socket_timeout=5,
    socket_connect_timeout=5,
    retry_on_timeout=True
)
cache = redis.Redis(connection_pool=redis_pool, decode_responses=True)

# Cache configuration
CACHE_VERSION = "1.1"  # Increment version to invalidate old cache
MAX_RETRIES = 3
BASE_BACKOFF = 0.1  # 100ms
DRAWING_CACHE_TIMEOUT = 3600  # 1 hour
ROOM_CACHE_TIMEOUT = 86400   # 24 hours
USER_PRESENCE_TIMEOUT = 300  # 5 minutes
PREFETCH_THRESHOLD = 10      # Number of accesses before prefetching

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
                    app.logger.error(f"Redis connection failed after {MAX_RETRIES} attempts: {str(e)}")
                    raise
                backoff = BASE_BACKOFF * (2 ** attempt)
                app.logger.warning(f"Redis connection failed (attempt {attempt + 1}): {str(e)}. Retrying in {backoff}s")
                time.sleep(backoff)
            except redis.RedisError as e:
                app.logger.error(f"Redis operation error: {str(e)}")
                raise
            except Exception as e:
                app.logger.error(f"Unexpected cache error: {str(e)}")
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
        app.logger.info(f"Cache {cache_status} - Key: {kwargs.get('cache_key')} - Duration: {duration:.3f}s")
        
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
                return cache.get(cache_key)
            
            @retry_with_backoff
            def set_cached_data(data):
                cache.set(cache_key, data, timeout=timeout)
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
        cache.set(
            pattern_key,
            {
                'last_access': datetime.utcnow().isoformat(),
                'access_count': cache.get(pattern_key, {}).get('access_count', 0) + 1
            },
            timeout=86400  # 24 hours
        )
    except Exception as e:
        app.logger.warning(f"Failed to update access pattern: {str(e)}")

def prefetch_room_data(room_id):
    """Prefetch room data based on access patterns"""
    pattern_key = f"access_pattern:{room_id}"
    try:
        pattern = cache.get(pattern_key)
        if pattern and pattern.get('access_count', 0) > PREFETCH_THRESHOLD:
            # Room is frequently accessed, prefetch related data
            app.logger.info(f"Prefetching data for frequently accessed room: {room_id}")
            
            # Prefetch drawing data
            from models import DrawingData
            drawings = DrawingData.query.filter_by(room_id=room_id).all()
            if drawings:
                try:
                    drawing_data = [json.loads(d.data) for d in drawings]
                    cache_key = get_cache_key(f"drawing_data_{room_id}")
                    
                    @retry_with_backoff
                    def cache_drawings():
                        cache.setex(
                            cache_key,
                            DRAWING_CACHE_TIMEOUT,
                            json.dumps(drawing_data, separators=(',', ':'))
                        )
                    
                    cache_drawings()
                    app.logger.info(f"Successfully prefetched {len(drawing_data)} drawings for room {room_id}")
                except json.JSONDecodeError as e:
                    app.logger.error(f"Error parsing drawing data during prefetch: {e}")
    except Exception as e:
        app.logger.warning(f"Failed to prefetch room data: {str(e)}")

def cache_room_state(room_id, state_data, timeout=ROOM_CACHE_TIMEOUT):
    """Cache room state including viewport and active users"""
    try:
        cache_key = get_cache_key(f"room_state_{room_id}")
        cache.setex(
            cache_key,
            timeout,
            json.dumps(state_data, separators=(',', ':'))
        )
        app.logger.info(f"Cached room state for room {room_id}")
    except Exception as e:
        app.logger.error(f"Failed to cache room state: {str(e)}")

def get_room_state(room_id):
    """Retrieve cached room state"""
    try:
        cache_key = get_cache_key(f"room_state_{room_id}")
        data = cache.get(cache_key)
        if data:
            return json.loads(data)
    except Exception as e:
        app.logger.error(f"Failed to get room state: {str(e)}")
    return None

def track_user_presence(room_id, user_id, user_data):
    """Track user presence in a room"""
    try:
        presence_key = get_cache_key(f"presence_{room_id}")
        user_key = f"user:{user_id}"
        
        # Update user data in room
        cache.hset(presence_key, user_key, json.dumps(user_data))
        # Set expiration for user presence
        cache.expire(presence_key, USER_PRESENCE_TIMEOUT)
        
        app.logger.info(f"Updated presence for user {user_id} in room {room_id}")
    except Exception as e:
        app.logger.error(f"Failed to track user presence: {str(e)}")

def get_active_users(room_id):
    """Get all active users in a room"""
    try:
        presence_key = get_cache_key(f"presence_{room_id}")
        user_data = cache.hgetall(presence_key)
        return {k: json.loads(v) for k, v in user_data.items()}
    except Exception as e:
        app.logger.error(f"Failed to get active users: {str(e)}")
        return {}

def invalidate_room_cache(room_id):
    """Invalidate all cached data for a room"""
    try:
        # Get all keys for the room
        room_pattern = get_cache_key(f"*_{room_id}")
        keys = cache.keys(room_pattern)
        
        if keys:
            cache.delete(*keys)
            app.logger.info(f"Invalidated cache for room {room_id}")
    except Exception as e:
        app.logger.error(f"Failed to invalidate room cache: {str(e)}")

def check_redis_connection():
    """Check Redis connection health"""
    try:
        cache.ping()
        return True
    except redis.ConnectionError as e:
        app.logger.error(f"Redis connection error: {str(e)}")
        return False
