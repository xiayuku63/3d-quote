FROM ubuntu:24.04

ARG BAMBU_VERSION=02.06.00.51
ARG BAMBU_MIRROR=https://mirror.ghproxy.com/

RUN sed -i 's|http://archive.ubuntu.com|http://mirrors.aliyun.com|g; s|http://security.ubuntu.com|http://mirrors.aliyun.com|g' /etc/apt/sources.list.d/ubuntu.sources && \
    apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-venv python3-pip \
    curl ca-certificates \
    xvfb libfuse2t64 \
    libwebkit2gtk-4.1-0 libosmesa6 \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

RUN set -e; \
    DOWNLOAD_OK=0; \
    for url in \
        "${BAMBU_MIRROR}https://github.com/bambulab/BambuStudio/releases/download/v${BAMBU_VERSION}/Bambu_Studio_linux_ubuntu-v${BAMBU_VERSION}.AppImage" \
        "https://github.com/bambulab/BambuStudio/releases/download/v${BAMBU_VERSION}/Bambu_Studio_linux_ubuntu-v${BAMBU_VERSION}.AppImage"; \
    do \
        echo "Downloading: $url"; \
        if curl -fsSL --connect-timeout 15 --max-time 300 -o /tmp/bambu.AppImage "$url"; then \
            SIZE=$(stat -c%s /tmp/bambu.AppImage 2>/dev/null || echo 0); \
            if [ "$SIZE" -gt 10000000 ]; then \
                echo "OK (${SIZE} bytes)"; \
                DOWNLOAD_OK=1; \
                break; \
            fi; \
            rm -f /tmp/bambu.AppImage; \
        fi; \
    done; \
    [ "$DOWNLOAD_OK" = "1" ] || { echo "ERROR: all download mirrors failed"; exit 1; }

RUN chmod +x /tmp/bambu.AppImage && \
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
