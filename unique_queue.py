from collections import deque
import threading

class UniqueQueue:
    def __init__(self, key_fn):
        self.q = deque()
        self.seen = set()
        self.lock = threading.Lock()
        self.not_empty = threading.Condition(self.lock)
        self.key_fn = key_fn

    def put(self, item):
        key = self.key_fn(item)
        with self.lock:
            if key not in self.seen:
                self.q.append(item)
                self.seen.add(key)
                self.not_empty.notify()

    def get(self):
        with self.not_empty:
            while not self.q:
                self.not_empty.wait()
            item = self.q.popleft()
            return item
        
    def done(self, item):
        key = self.key_fn(item)
        with self.lock:
            if key in self.seen:
                self.seen.remove(key)
            else:
                raise ValueError("done() called on unknown item")
                
        
    def check(self, item):
        key = self.key_fn(item)
        with self.lock:
            return key in self.seen