from app import cache
from functools import wraps

def cache_drawing(timeout=300):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            room_id = kwargs.get('room_id')
            cache_key = f"drawing_{room_id}"
            
            # Try to get cached data
            cached_data = cache.get(cache_key)
            if cached_data is not None:
                return cached_data
            
            # Get fresh data
            data = f(*args, **kwargs)
            if data is not None:
                # Store in cache with timeout
                cache.set(cache_key, data, timeout=timeout)
            return data
            
        return decorated_function
    return decorator
