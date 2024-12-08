import logging
import time
from functools import wraps
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

class CircuitBreaker:
    def __init__(self, failure_threshold=5, reset_timeout=60):
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self.failures = 0
        self.last_failure_time = None
        self.state = "CLOSED"  # CLOSED, OPEN, HALF_OPEN

    def can_execute(self):
        if self.state == "CLOSED":
            return True
        
        if self.state == "OPEN":
            if self.last_failure_time and \
               datetime.now() - self.last_failure_time > timedelta(seconds=self.reset_timeout):
                self.state = "HALF_OPEN"
                return True
            return False
            
        return True  # HALF_OPEN state allows execution

    def record_success(self):
        self.failures = 0
        if self.state == "HALF_OPEN":
            self.state = "CLOSED"

    def record_failure(self):
        self.failures += 1
        self.last_failure_time = datetime.now()
        if self.failures >= self.failure_threshold:
            self.state = "OPEN"

    def __call__(self, func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            if not self.can_execute():
                logger.warning(f"Circuit breaker is {self.state}, skipping cache operation")
                return None
                
            try:
                result = func(*args, **kwargs)
                self.record_success()
                return result
            except Exception as e:
                self.record_failure()
                logger.error(f"Cache operation failed: {str(e)}")
                return None
                
        return wrapper
