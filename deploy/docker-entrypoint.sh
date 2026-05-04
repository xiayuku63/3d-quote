#!/bin/bash
set -e

echo "[pricer3d] Bambu Studio: $(which bambu-studio || echo NOT FOUND)"
echo "[pricer3d] Profile dir:  ${BAMBU_PROFILE_DIR:-/app/profiles/bambu}"
echo "[pricer3d] Data dir:     ${DB_PATH:-/app/data/app.db}"
echo "[pricer3d] Env:          ${APP_ENV:-development}"

if [ ! -d "${BAMBU_PROFILE_DIR:-/app/profiles/bambu}" ]; then
    echo "[pricer3d] WARN: profile directory not found, slicer may fail"
fi

exec /app/venv/bin/uvicorn main:app --host 0.0.0.0 --port 5000
