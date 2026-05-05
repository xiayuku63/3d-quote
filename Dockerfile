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

RUN file /tmp/bambu.AppImage | grep -q "ELF" || { echo "ERROR: bambu.AppImage is not a valid binary"; ls -la /tmp/bambu.AppImage; file /tmp/bambu.AppImage; exit 1; } && \
    SIZE=$(stat -c%s /tmp/bambu.AppImage); echo "AppImage size: ${SIZE} bytes"; \
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
