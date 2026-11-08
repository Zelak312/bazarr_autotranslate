from collections import deque
import threading


class UniqueQueue:
    """
    A thread-safe FIFO queue that drops duplicate items.

    An item is a duplicate when key_fn(item) was already seen and has not
    been released via done() yet. None is reserved as a shutdown sentinel:
    it bypasses deduplication and is returned by get() as-is so that
    workers can exit cleanly.
    """

    def __init__(self, key_fn):
        self.q = deque()
        self.seen = set()
        self.lock = threading.Lock()
        self.not_empty = threading.Condition(self.lock)
        self.key_fn = key_fn

    def put(self, item):
        with self.lock:
            if item is None:
                # Shutdown sentinel: always enqueue, never deduplicate
                self.q.append(item)
                self.not_empty.notify()
                return
            key = self.key_fn(item)
            if key not in self.seen:
                self.q.append(item)
                self.seen.add(key)
                self.not_empty.notify()

    def get(self):
        with self.not_empty:
            while not self.q:
                self.not_empty.wait()
            return self.q.popleft()

    def done(self, item):
        if item is None:
            return
        key = self.key_fn(item)
        with self.lock:
            # Idempotent: an unknown key must not raise. Raising here could
            # kill a worker thread, and a key stuck in `seen` would permanently
            # block that video/language from ever being queued again.
            self.seen.discard(key)

    def check_key(self, key):
        """
        Return True when a task with this key is queued or currently being
        processed (i.e. not yet released via done()).
        """
        with self.lock:
            return key in self.seen
