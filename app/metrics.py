"""In-memory metrics tracker."""

import threading
import time
from collections import defaultdict, deque


class InMemoryMetrics:
    def __init__(self):
        self._lock = threading.Lock()
        self._events: deque[dict] = deque()
        self._max: int = 2000

    def set_max_events(self, n: int) -> None:
        self._max = max(1, int(n))

    def _normalize_path(self, path: str) -> str:
        return str(path or "/").strip() or "/"

    def record(self, path: str, status_code: int, duration_ms: float) -> None:
        now = time.time()
        entry = {
            "t": now,
            "p": self._normalize_path(path),
            "s": int(status_code or 0),
            "ms": round(float(duration_ms or 0.0), 2),
        }
        with self._lock:
            self._events.append(entry)
            while len(self._events) > self._max:
                self._events.popleft()

    def snapshot(self) -> dict:
        with self._lock:
            events = list(self._events)
        now = time.time()
        recent_events = [e for e in events if now - float(e.get("t") or 0) <= 3600.0]
        path_aggregates: dict = {}
        for e in recent_events:
            path = str(e.get("p") or "/")
            if path not in path_aggregates:
                path_aggregates[path] = {"count": 0, "5xx": 0, "lat_sum": 0.0}
            ag = path_aggregates[path]
            ag["count"] += 1
            if int(e.get("s") or 0) >= 500:
                ag["5xx"] += 1
            ag["lat_sum"] += float(e.get("ms") or 0.0)
        recent_minute = [e for e in events if now - float(e.get("t") or 0) <= 60.0]
        path_stats = []
        for p, ag in sorted(path_aggregates.items(), key=lambda x: -x[1]["count"]):
            avg = round((float(ag.get("lat_sum") or 0.0) / max(1.0, float(ag.get("count") or 1.0))), 2)
            path_stats.append(
                {
                    "path": p,
                    "count": int(ag.get("count") or 0),
                    "errors_5xx": int(ag.get("5xx") or 0),
                    "avg_latency_ms": avg,
                }
            )
        return {
            "total_events": len(events),
            "max_events": self._max,
            "requests_last_min": len(recent_minute),
            "requests_last_hour": len(recent_events),
            "path_stats": path_stats[:50],
        }
