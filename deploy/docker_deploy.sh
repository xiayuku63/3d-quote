#!/usr/bin/env bash
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; BLUE='\033[1;34m'; NC='\033[0m'

log()  { echo -e "${BLUE}[$(date +%H:%M:%S)]${NC} $*"; }
ok()   { echo -e "${GREEN}[OK]${NC} $*"; }
err()  { echo -e "${RED}[ERR]${NC} $*"; }

cd "$(dirname "$0")/.."
log "3D Printing Quoting System — Docker Deploy"

# 1. Pull latest code
log "Pulling latest code..."
git pull origin main

# 2. Check Bambu AppImage
if [ -f bambu.AppImage ]; then
    SIZE=$(stat -c%s bambu.AppImage 2>/dev/null || echo 0)
    if [ "$SIZE" -gt 10000000 ]; then
        ok "bambu.AppImage: ${SIZE} bytes"
    else
        err "bambu.AppImage too small (${SIZE} bytes), trying download..."
        rm -f bambu.AppImage
    fi
fi

if [ ! -f bambu.AppImage ]; then
    log "Downloading Bambu Studio AppImage..."
    if [ -f deploy/download_bambu.sh ]; then
        bash deploy/download_bambu.sh || {
            err "Auto-download failed."
            echo "  Download from: https://github.com/bambulab/BambuStudio/releases"
            echo "  Rename to bambu.AppImage and scp to this directory."
            echo "  scp bambu.AppImage root@47.106.102.208:~/3d-quote/"
            exit 1
        }
    else
        err "download_bambu.sh not found!"
        exit 1
    fi
fi

# 3. Build & start
log "Building Docker image..."
docker compose build

log "Starting services..."
docker compose up -d

# 4. Status
sleep 2
log "Service status:"
docker compose ps

echo ""
if docker compose exec -T app /app/venv/bin/python -c "import sys; sys.exit(0)" 2>/dev/null; then
    ok "Deploy successful!"
    echo "  Check: curl http://127.0.0.1/healthz"
else
    err "App container may not be ready, check logs:"
    echo "  docker compose logs app"
fi
