# pricer3d backup cron job — runs daily at 3 AM
# Add to crontab: crontab -e
#
# 0 3 * * * /bin/bash /path/to/pricer3d/deploy/backup_cron.sh >> /path/to/pricer3d/logs/backup_cron.log 2>&1

#!/bin/bash
set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

# Backup via API (requires admin token)
# Or use docker exec
if docker ps --format '{{.Names}}' | grep -q pricer3d-app; then
    echo "[$(date)] Creating backup..."
    docker exec pricer3d-app /app/venv/bin/python3 -c "
from app.backup import create_backup, cleanup_old_backups
try:
    info = create_backup()
    print(f'Backup created: {info[\"backup_name\"]} ({info[\"size_bytes\"]} bytes)')
except Exception as e:
    print(f'Backup failed: {e}')
    exit(1)
deleted = cleanup_old_backups()
if deleted:
    print(f'Cleaned up {deleted} old backups')
"
    echo "[$(date)] Backup complete"
else
    echo "[$(date)] Container not running, skipping backup"
fi
