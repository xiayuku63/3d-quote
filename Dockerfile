FROM ubuntu:24.04

ARG BAMBU_VERSION=02.06.00.51
ARG BAMBU_RELEASE=https://github.com/bambulab/BambuStudio/releases/download/v${BAMBU_VERSION}/Bambu_Studio_linux_ubuntu-v${BAMBU_VERSION}.AppImage

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-venv python3-pip \
    curl ca-certificates \
    xvfb libfuse2t64 \
    libwebkit2gtk-4.1-0 libosmesa6 \
    libavcodec61 libavformat61 libswscale8 libavutil59 \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL -o /tmp/bambu.AppImage "${BAMBU_RELEASE}" && \
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
