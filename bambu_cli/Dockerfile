# bambu_cli/Dockerfile
# Multi-stage build for bambu_cli (pure CLI BambuStudio slicer).
#
# Stage 1: Build from source (needs full BambuStudio source tree with
#           bambu_cli/ directory added)
# Stage 2: Minimal runtime
#
# Build:
#   docker build -t bambu_cli -f bambu_cli/Dockerfile .

FROM ubuntu:24.04 AS builder

# ---- Dependencies ----
RUN sed -i 's|http://archive.ubuntu.com|http://mirrors.aliyun.com|g; s|http://security.ubuntu.com|http://mirrors.aliyun.com|g' /etc/apt/sources.list.d/ubuntu.sources && \
    apt-get update && apt-get install -y --no-install-recommends \
    build-essential cmake git \
    libboost-all-dev \
    libtbb-dev \
    libcurl4-openssl-dev \
    libeigen3-dev \
    libnlopt-cxx-dev \
    libgmp-dev libmpfr-dev \
    libblas-dev liblapack-dev \
    libpng-dev libjpeg-dev \
    libexpat1-dev \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# C++17 compiler
# Ubuntu 24.04 ships GCC 13+

WORKDIR /build

# ---- Copy source ----
# Assumes BambuStudio source is at the repo root, and bambu_cli/ is a subdirectory
COPY . .

# ---- Build libslic3r first ----
RUN mkdir -p build_slicer && cd build_slicer && \
    cmake .. \
        -DCMAKE_BUILD_TYPE=Release \
        -DSLIC3R_BUILD_CLI=ON \
        -DSLIC3R_BUILD_GUI=OFF \
        -DSLIC3R_STATIC=OFF \
        -DSLIC3R_FHS=ON \
        -DCMAKE_INSTALL_PREFIX=/usr/local \
        -DSLIC3R_WX_STABLE=OFF \
    && cmake --build . --target libslic3r -j$(nproc)

# ---- Build bambu_cli ----
RUN cd build_slicer && \
    cmake --build . --target bambu_cli -j$(nproc)

# ---- Verify ----
RUN build_slicer/bambu_cli --version

# ===========================================================================
# Stage 2: Runtime
# ===========================================================================
FROM ubuntu:24.04 AS runtime

RUN sed -i 's|http://archive.ubuntu.com|http://mirrors.aliyun.com|g; s|http://security.ubuntu.com|http://mirrors.aliyun.com|g' /etc/apt/sources.list.d/ubuntu.sources && \
    apt-get update && apt-get install -y --no-install-recommends \
    libboost-filesystem1.83.0 \
    libtbb12 \
    libcurl4 \
    libnlopt-cxx0 \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /build/build_slicer/bambu_cli /usr/local/bin/bambu_cli

# Default profiles directory (mount or copy your Bambu JSON profiles here)
RUN mkdir -p /profiles

WORKDIR /work
ENTRYPOINT ["/usr/local/bin/bambu_cli"]
