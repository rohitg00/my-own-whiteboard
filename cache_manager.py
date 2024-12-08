import os
import json
import logging
from datetime import datetime
from redis import Redis, ConnectionPool
from redis.exceptions import ConnectionError, RedisError
from redis.retry import Retry
from redis.backoff import ExponentialBackoff
from functools import wraps

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Constants
USER_PRESENCE_TIMEOUT = 30  # seconds
CACHE_EXPIRY = 3600  # 1 hour
CURSOR_POSITION_TIMEOUT = 5  # seconds
REDIS_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
CACHE_VERSION = "1.1"
MAX_RETRIES = 3
BASE_BACKOFF = 0.1
DRAWING_CACHE_TIMEOUT = 3600  # 1 hour
ROOM_CACHE_TIMEOUT = 86400   # 24 hours
PREFETCH_THRESHOLD = 10      # Number of accesses before prefetching


# Initialize Redis connection pool
redis_pool = ConnectionPool.from_url(
    REDIS_URL,
    max_connections=10,  # Limit max connections
    retry=Retry(ExponentialBackoff(), 3),  # Retry 3 times with exponential backoff
    decode_responses=True  # Automatically decode responses to strings
)

# Create Redis client with connection pooling
redis_client = Redis(connection_pool=redis_pool)

def get_cache_key(key, version=CACHE_VERSION):
    """Generate a namespaced cache key"""
    return f"whiteboard:{key}:v{version}"

def retry_with_backoff(func):
    """Decorator to retry Redis operations with exponential backoff"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        max_retries = 3
        retry_count = 0
        while retry_count < max_retries:
            try:
                return func(*args, **kwargs)
            except (ConnectionError, RedisError) as e:
                retry_count += 1
                if retry_count == max_retries:
                    logger.error(f"Redis operation failed after {max_retries} retries: {str(e)}")
                    raise
                wait_time = 2 ** retry_count  # Exponential backoff
                logger.warning(f"Redis operation failed, retrying in {wait_time}s: {str(e)}")
                import time
                time.sleep(wait_time)
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
        logger.info(f"Cache {cache_status} - Key: {kwargs.get('cache_key')} - Duration: {duration:.3f}s")
        
        return result
    return wrapper


@retry_with_backoff
def cache_room_state(room_id, state_data):
    """Cache room state with retry logic"""
    try:
        state_key = get_cache_key(f"state_{room_id}")
        redis_client.hset(state_key, mapping=state_data)
        redis_client.expire(state_key, CACHE_EXPIRY)
        logger.info(f"Cached room state for room {room_id}")
    except Exception as e:
        logger.error(f"Failed to cache room state: {str(e)}")

@retry_with_backoff
def get_room_state(room_id):
    """Get room state from cache"""
    try:
        state_key = get_cache_key(f"state_{room_id}")
        return redis_client.hgetall(state_key) or {}
    except Exception as e:
        logger.error(f"Failed to get room state: {str(e)}")
        return {}

@retry_with_backoff
def track_user_presence(room_id, user_id, user_data):
    """Track user presence with automatic cleanup"""
    try:
        presence_key = get_cache_key(f"presence_{room_id}")
        user_data['last_seen'] = datetime.utcnow().isoformat()
        redis_client.hset(presence_key, str(user_id), json.dumps(user_data))
        redis_client.expire(presence_key, USER_PRESENCE_TIMEOUT)
        logger.info(f"Updated presence for user {user_id} in room {room_id}")
    except Exception as e:
        logger.error(f"Failed to track user presence: {str(e)}")

@retry_with_backoff
def get_active_users(room_id):
    """Get active users with cleanup of stale data"""
    try:
        presence_key = get_cache_key(f"presence_{room_id}")
        users_data = redis_client.hgetall(presence_key)
        active_users = {}
        
        for user_id, data in users_data.items():
            try:
                user_data = json.loads(data)
                last_seen = datetime.fromisoformat(user_data.get('last_seen', '2000-01-01'))
                if (datetime.utcnow() - last_seen).total_seconds() <= USER_PRESENCE_TIMEOUT:
                    active_users[user_id] = user_data
            except (json.JSONDecodeError, ValueError):
                continue
                
        return active_users
    except Exception as e:
        logger.error(f"Failed to get active users: {str(e)}")
        return {}

@retry_with_backoff
def prefetch_room_data(room_id):
    """Prefetch commonly accessed room data"""
    try:
        pipeline = redis_client.pipeline()
        state_key = get_cache_key(f"state_{room_id}")
        presence_key = get_cache_key(f"presence_{room_id}")
        drawing_key = get_cache_key(f"drawing_data_{room_id}")
        
        pipeline.exists(state_key)
        pipeline.exists(presence_key)
        pipeline.exists(drawing_key)
        pipeline.execute()
    except Exception as e:
        logger.error(f"Failed to prefetch room data: {str(e)}")

@retry_with_backoff
def cache_cursor_position(room_id, user_id, position_data):
    """Cache cursor position with rate limiting and pooling"""
    try:
        cursor_key = get_cache_key(f"cursor_{room_id}_{user_id}")
        redis_client.setex(cursor_key, CURSOR_POSITION_TIMEOUT, json.dumps(position_data))
    except Exception as e:
        logger.error(f"Failed to cache cursor position: {str(e)}")

@retry_with_backoff
def get_cursor_positions(room_id):
    """Get all active cursor positions in a room"""
    try:
        pattern = get_cache_key(f"cursor_{room_id}_*")
        cursor_keys = redis_client.keys(pattern)
        positions = {}
        
        pipeline = redis_client.pipeline()
        for key in cursor_keys:
            pipeline.get(key)
        
        results = pipeline.execute()
        for key, data in zip(cursor_keys, results):
            if data:
                user_id = key.split('_')[-1]
                try:
                    positions[user_id] = json.loads(data)
                except json.JSONDecodeError:
                    continue
        
        return positions
    except Exception as e:
        logger.error(f"Failed to get cursor positions: {str(e)}")
        return {}

def check_redis_connection():
    """Check Redis connection health"""
    try:
        return redis_client.ping()
    except Exception as e:
        logger.error(f"Redis connection error: {e}")
        return False

# Export the redis client as cache
cache = redis_client

def update_access_pattern(room_id):
    """Track room access patterns for prefetching"""
    pattern_key = get_cache_key(f"access_pattern:{room_id}")
    try:
        redis_client.incr(pattern_key)
        redis_client.expire(pattern_key, 86400) # 24 hours
    except Exception as e:
        logger.warning(f"Failed to update access pattern: {str(e)}")


def cache_drawing(timeout=DRAWING_CACHE_TIMEOUT):
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
                redis_client.set(cache_key, data, ex=timeout)
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

def prefetch_room_data(room_id):
    """Prefetch room data based on access patterns"""
    pattern_key = get_cache_key(f"access_pattern:{room_id}")
    try:
        access_count = int(redis_client.get(pattern_key) or 0)
        if access_count > PREFETCH_THRESHOLD:
            # Room is frequently accessed, prefetch related data
            logger.info(f"Prefetching data for frequently accessed room: {room_id}")
            
            # Prefetch drawing data (Example - adapt to your DrawingData model)
            # from models import DrawingData # Assumed this import is available
            # drawings = DrawingData.query.filter_by(room_id=room_id).all()
            # if drawings:
            #     try:
            #         drawing_data = [json.loads(d.data) for d in drawings]
            #         cache_key = get_cache_key(f"drawing_data_{room_id}")
            #         
            #         @retry_with_backoff
            #         def cache_drawings():
            #             redis_client.setex(
            #                 cache_key,
            #                 DRAWING_CACHE_TIMEOUT,
            #                 json.dumps(drawing_data, separators=(',', ':'))
            #             )
            #         
            #         cache_drawings()
            #         logger.info(f"Successfully prefetched {len(drawing_data)} drawings for room {room_id}")
            #     except json.JSONDecodeError as e:
            #         logger.error(f"Error parsing drawing data during prefetch: {e}")
    except Exception as e:
        logger.warning(f"Failed to prefetch room data: {str(e)}")

@retry_with_backoff
def invalidate_room_cache(room_id):
    """Invalidate all cached data for a room"""
    try:
        # Get all keys for the room
        room_pattern = get_cache_key(f"*_{room_id}")
        keys = redis_client.keys(room_pattern)
        
        if keys:
            redis_client.delete(*keys)
            logger.info(f"Invalidated cache for room {room_id}")
    except Exception as e:
        logger.error(f"Failed to invalidate room cache: {str(e)}")
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
        logger.error(f"Failed to cleanup disconnected users: {str(e)}")