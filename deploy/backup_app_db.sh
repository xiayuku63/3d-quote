set -euo pipefail

APP_DIR="${APP_DIR:-/opt/pricer3d}"
DB_PATH="${DB_PATH:-$APP_DIR/app.db}"
BACKUP_DIR="${BACKUP_DIR:-$APP_DIR/backups}"

mkdir -p "$BACKUP_DIR"

ts="$(date -u +%Y%m%dT%H%M%SZ)"
out="$BACKUP_DIR/app.db.$ts.sqlite"

sqlite3 "$DB_PATH" ".backup '$out'"

echo "$out"
