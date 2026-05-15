"""Database backup utilities."""

import os
import shutil
import time
import logging
from datetime import datetime, timezone

from .config import DB_PATH

logger = logging.getLogger("pricer3d")

BACKUP_DIR = os.getenv("BACKUP_DIR", "backups").strip() or "backups"
BACKUP_RETENTION_DAYS = int(os.getenv("BACKUP_RETENTION_DAYS", "30") or "30")


def _ensure_backup_dir() -> str:
    os.makedirs(BACKUP_DIR, exist_ok=True)
    return BACKUP_DIR


def create_backup() -> dict:
    """Create a timestamped backup of the SQLite database. Returns backup info."""
    if not os.path.exists(DB_PATH):
        raise FileNotFoundError(f"Database not found at {DB_PATH}")

    _ensure_backup_dir()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    db_name = os.path.basename(DB_PATH)
    backup_name = f"{db_name}.{timestamp}.bak"
    backup_path = os.path.join(BACKUP_DIR, backup_name)

    # Use SQLite backup API for safe copy
    import sqlite3
    src = sqlite3.connect(DB_PATH)
    dst = sqlite3.connect(backup_path)
    src.backup(dst)
    dst.close()
    src.close()

    file_size = os.path.getsize(backup_path)
    logger.info("event=backup_created path=%s size=%d", backup_path, file_size)

    return {
        "backup_path": backup_path,
        "backup_name": backup_name,
        "size_bytes": file_size,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def list_backups() -> list[dict]:
    """List all backups sorted by creation time (newest first)."""
    _ensure_backup_dir()
    backups = []
    for fname in os.listdir(BACKUP_DIR):
        if not fname.endswith(".bak"):
            continue
        fpath = os.path.join(BACKUP_DIR, fname)
        try:
            stat = os.stat(fpath)
            backups.append({
                "name": fname,
                "path": fpath,
                "size_bytes": stat.st_size,
                "mtime_iso": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            })
        except OSError:
            continue
    backups.sort(key=lambda b: b["mtime_iso"], reverse=True)
    return backups


def cleanup_old_backups() -> int:
    """Remove backups older than BACKUP_RETENTION_DAYS. Returns number deleted."""
    _ensure_backup_dir()
    cutoff = time.time() - (BACKUP_RETENTION_DAYS * 86400)
    deleted = 0
    for fname in os.listdir(BACKUP_DIR):
        if not fname.endswith(".bak"):
            continue
        fpath = os.path.join(BACKUP_DIR, fname)
        try:
            if os.path.getmtime(fpath) < cutoff:
                os.remove(fpath)
                deleted += 1
                logger.info("event=backup_cleaned path=%s", fpath)
        except OSError:
            continue
    return deleted
