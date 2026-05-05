#!/usr/bin/env bash
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'

VERSION="${1:-02.06.00.51}"
APPIMAGE_NAME="${2:-Bambu_Studio_linux_ubuntu-v${VERSION}.AppImage}"

cd "$(dirname "$0")/.."

if [ -f bambu.AppImage ]; then
    SIZE=$(stat -c%s bambu.AppImage 2>/dev/null || echo 0)
    if [ "$SIZE" -gt 10000000 ]; then
        echo -e "${GREEN}[OK] bambu.AppImage already exists (${SIZE} bytes)${NC}"
        exit 0
    fi
    rm -f bambu.AppImage
fi

URLS=(
    "https://ghproxy.net/https://github.com/bambulab/BambuStudio/releases/download/v${VERSION}/${APPIMAGE_NAME}"
    "https://gh.api.99988866.xyz/https://github.com/bambulab/BambuStudio/releases/download/v${VERSION}/${APPIMAGE_NAME}"
    "https://github.com/bambulab/BambuStudio/releases/download/v${VERSION}/${APPIMAGE_NAME}"
)

echo "Attempting to download Bambu Studio v${VERSION} ..."

for url in "${URLS[@]}"; do
    TMP=$(mktemp /tmp/bambu_dl_XXXXXX)
    echo -e "  Trying: ${YELLOW}${url}${NC}"
    if wget -q --timeout=30 --tries=1 -O "$TMP" "$url" 2>/dev/null; then
        SIZE=$(stat -c%s "$TMP" 2>/dev/null || echo 0)
        if [ "$SIZE" -gt 10000000 ]; then
            mv "$TMP" bambu.AppImage
            echo -e "${GREEN}[OK] Downloaded ${SIZE} bytes${NC}"
            exit 0
        fi
    fi
    rm -f "$TMP"
    echo "  Failed, trying next..."
done

echo ""
echo -e "${RED}All download attempts failed.${NC}"
echo ""
echo "Please download the AppImage manually:"
echo "  1. On your PC, visit: https://github.com/bambulab/BambuStudio/releases"
echo "  2. Download: ${APPIMAGE_NAME}"
echo "  3. Rename it to 'bambu.AppImage'"
echo "  4. SCP it to: $(pwd)/bambu.AppImage"
echo "  scp bambu.AppImage root@47.106.102.208:~/3d-quote/"
echo ""
echo "Then re-run: docker compose up -d --build"
