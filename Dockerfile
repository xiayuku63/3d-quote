FROM ubuntu:24.04

RUN sed -i 's|http://archive.ubuntu.com|http://mirrors.aliyun.com|g; s|http://security.ubuntu.com|http://mirrors.aliyun.com|g' /etc/apt/sources.list.d/ubuntu.sources && \
    apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-venv python3-pip \
    ca-certificates \
    xvfb libfuse2t64 \
    libwebkit2gtk-4.1-0 libosmesa6 \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY bambu.AppImage /tmp/

RUN SIZE=$(stat -c%s /tmp/bambu.AppImage 2>/dev/null || echo 0); \
    if [ "$SIZE" -lt 10000000 ]; then \
        echo "ERROR: bambu.AppImage too small or missing (${SIZE} bytes)"; \
        echo "Please download it first:"; \
        echo "  On your PC, visit: https://github.com/bambulab/BambuStudio/releases"; \
        echo "  Download: Bambu_Studio_linux_ubuntu-v02.06.00.51.AppImage"; \
        echo "  Rename to bambu.AppImage"; \
        echo '  scp bambu.AppImage root@47.106.102.208:~/3d-quote/'; \
        exit 1; \
    fi; \
    echo "AppImage size: ${SIZE} bytes"; \
    chmod +x /tmp/bambu.AppImage && \
    cd /tmp && /tmp/bambu.AppImage --appimage-extract && \
    mkdir -p /opt/bambu-studio && \
    cp -r /tmp/squashfs-root/* /opt/bambu-studio/ && \
    rm -rf /tmp/squashfs-root /tmp/bambu.AppImage

RUN BIN=$(find /opt/bambu-studio -name "bambu-studio" -type f | head -1) && \
    [ -n "$BIN" ] || { echo "ERROR: bambu-studio binary not found"; exit 1; } && \
    chmod +x "$BIN" && ln -sf "$BIN" /usr/local/bin/bambu-studio

WORKDIR /app
COPY requirements.txt .
RUN python3 -m venv /app/venv && \
    /app/venv/bin/pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/data/uploads /app/data/outputs /app/data/user

COPY deploy/docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

EXPOSE 5000
ENTRYPOINT ["docker-entrypoint.sh"]
