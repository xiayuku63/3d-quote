"""Database-backed rate limiter with in-memory cache for performance."""

import time
import threading
from collections import defaultdict, deque


class PersistentRateLimiter:
    """Hybrid rate limiter: fast in-memory check + optional DB persistence."""

    def __init__(self):
        self._buckets: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()
        self._flush_interval = 60  # seconds between DB flushes
        self._last_flush: dict[str, float] = {}

    def is_allowed(self, key: str, limit: int, window_seconds: int = 60) -> bool:
        now = time.time()
        with self._lock:
            bucket = self._buckets[key]
            while bucket and (now - bucket[0]) > window_seconds:
                bucket.popleft()
            if len(bucket) >= limit:
                # Persist blocked event
                self._maybe_persist(key, limit, window_seconds, len(bucket))
                return False
            bucket.append(now)
            self._maybe_persist(key, limit, window_seconds, len(bucket))
            return True

    def _maybe_persist(self, key: str, limit: int, window: int, count: int) -> None:
        """Flush state to DB periodically."""
        now = time.time()
        last = self._last_flush.get(key, 0)
        if now - last < self._flush_interval and count < limit:
            return
        self._last_flush[key] = now
        try:
            from .database import get_db_conn
            with get_db_conn() as conn:
                conn.execute(
                    """INSERT OR REPLACE INTO rate_limit_state (rate_key, bucket_json, updated_at)
                       VALUES (?, ?, ?)""",
                    (key[:200], f'{{"count":{count},"limit":{limit},"window":{window}}}',
                     time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(now))),
                )
                conn.commit()
        except Exception:
            pass

    def restore_state(self) -> None:
        """Restore rate limit state from DB on startup."""
        try:
            from .database import get_db_conn
            now = time.time()
            with get_db_conn() as conn:
                rows = conn.execute(
                    "SELECT rate_key, bucket_json FROM rate_limit_state WHERE CAST(updated_at AS REAL) > ?",
                    (str(now - 7200),),  # Last 2 hours
                ).fetchall()
            with self._lock:
                for row in rows:
                    try:
                        import json
                        data = json.loads(row["bucket_json"])
                        count = int(data.get("count", 0))
                        if count > 0:
                            # Pre-populate bucket with approximate timestamps
                            key = row["rate_key"]
                            bucket = self._buckets[key]
                            spacing = float(data.get("window", 60)) / max(count, 1)
                            for i in range(count):
                                bucket.append(now - (count - i) * spacing)
                    except Exception:
                        pass
        except Exception:
            pass

    def get_state(self, key: str) -> dict:
        """Get current state for a key (for monitoring)."""
        now = time.time()
        with self._lock:
            bucket = self._buckets.get(key, deque())
            return {"key": key, "count": len(bucket), "pending": list(bucket)[:10]}
