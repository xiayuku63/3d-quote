set -euo pipefail

APP_DIR="${APP_DIR:-/opt/pricer3d}"
DB_PATH="${DB_PATH:-$APP_DIR/app.db}"

if [ $# -lt 1 ]; then
  echo "Usage: $0 /path/to/backup.sqlite" >&2
  exit 2
fi

backup="$1"
if [ ! -f "$backup" ]; then
  echo "Backup not found: $backup" >&2
  exit 2
fi

sqlite3 "$backup" "PRAGMA integrity_check;" | grep -q "^ok$"

tmp="$DB_PATH.restore.$$"
cp -f "$backup" "$tmp"
mv -f "$tmp" "$DB_PATH"

echo "restored:$DB_PATH"
