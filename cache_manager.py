from app import cache, app
from functools import wraps
import time
import logging
from datetime import datetime

CACHE_VERSION = "1.1"  # Increment version to invalidate old cache
MAX_RETRIES = 3
BASE_BACKOFF = 0.1  # 100ms

# Cache configuration
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
        if pattern and pattern.get('access_count', 0) > 10:
            # Room is frequently accessed, prefetch related data
            app.logger.info(f"Prefetching data for frequently accessed room: {room_id}")
            
            # Prefetch drawing data
            from models import DrawingData
            drawings = DrawingData.query.filter_by(room_id=room_id).all()
            if drawings:
                drawing_data = [eval(d.data) for d in drawings]
                cache_key = get_cache_key(f"drawing_data_{room_id}")
                
                @retry_with_backoff
                def cache_drawings():
                    cache.set(cache_key, drawing_data, timeout=600)  # 10 minutes
                
                cache_drawings()
                app.logger.info(f"Successfully prefetched {len(drawing_data)} drawings for room {room_id}")
    except Exception as e:
        app.logger.warning(f"Failed to prefetch room data: {str(e)}")
