"""Error notification — alerts on critical failures."""

import os
import logging
from collections import defaultdict, deque
import threading
import time

logger = logging.getLogger("pricer3d")

ERROR_NOTIFY_WEBHOOK = os.getenv("ERROR_NOTIFY_WEBHOOK", "").strip()
ERROR_NOTIFY_COOLDOWN_SECONDS = int(os.getenv("ERROR_NOTIFY_COOLDOWN_SECONDS", "300") or "300")


class ErrorNotifier:
    """Debounced error notifier — groups errors by type to avoid spam."""

    def __init__(self):
        self._lock = threading.Lock()
        self._last_notified: dict[str, float] = {}

    def notify(self, error_type: str, message: str, detail: dict = None) -> None:
        """Notify on critical error. Deduplicates within cooldown window."""
        if not ERROR_NOTIFY_WEBHOOK:
            return

        now = time.time()
        with self._lock:
            last = self._last_notified.get(error_type, 0)
            if now - last < ERROR_NOTIFY_COOLDOWN_SECONDS:
                return
            self._last_notified[error_type] = now

        try:
            import urllib.request
            import json
            payload = {
                "type": error_type,
                "message": message[:500],
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
            }
            if detail:
                payload["detail"] = detail

            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                ERROR_NOTIFY_WEBHOOK,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                logger.info("event=error_notification type=%s status=%s", error_type, resp.status)
        except Exception as e:
            logger.warning("event=error_notification_failed type=%s error=%s", error_type, str(e))


error_notifier = ErrorNotifier()


def notify_critical(error_type: str, message: str, detail: dict = None) -> None:
    """Shortcut to notify critical error."""
    error_notifier.notify(error_type, message, detail)
