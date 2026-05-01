#!/usr/bin/env bash
set -euo pipefail

#
# install_bambu_slicer.sh
# 自动下载并安装 Bambu Studio CLI（Headless 切片引擎）
#
# 用法:
#   bash install_bambu_slicer.sh          # 安装到默认路径 /opt/bambu-studio
#   bash install_bambu_slicer.sh --uninstall  # 卸载
#   INSTALL_DIR=/custom/path bash install_bambu_slicer.sh  # 自定义安装路径
#
# 环境变量:
#   INSTALL_DIR        安装目标目录 (默认 /opt/bambu-studio)
#   BAMBU_VERSION      指定版本号 (默认自动获取最新版)
#   GITHUB_TOKEN       避免 API 限流 (可选)
#   GITHUB_MIRRORS     逗号分隔的下载镜像 (默认 ghproxy.com,ghproxy.net)
#   BAMBU_DIRECT=1     跳过镜像，直连 GitHub 下载
#   SKIP_APT_DEPS      跳过系统依赖安装 (默认尝试安装)
#   PROFILE_SRC        拷贝 profiles 的源目录 (默认项目内 profiles/bambu/)
#

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
INSTALL_DIR="${INSTALL_DIR:-/opt/bambu-studio}"
APPIMAGE_NAME=""
EXTRACT_DIR=""
VERSION=""
DOWNLOAD_URL=""

# Detect distro for AppImage variant selection
detect_distro() {
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        case "${ID,,}" in
            ubuntu|debian|linuxmint|pop|elementary|zorin|kali|raspbian|neon)
                echo "ubuntu"
                return
                ;;
            fedora|rhel|centos|rocky|almalinux|ol)
                echo "fedora"
                return
                ;;
            *)
                echo "ubuntu"  # fallback
                return
                ;;
        esac
    fi
    echo "ubuntu"
}

DISTRO="$(detect_distro)"

# GitHub mirror config (default: use domestic mirror for faster download)
#   GITHUB_MIRRORS — 逗号分隔的下载镜像列表 (留空则直连 GitHub)
#   BAMBU_DIRECT=1  — 跳过所有镜像，直接连接 GitHub
GITHUB_MIRRORS="${GITHUB_MIRRORS-https://mirror.ghproxy.com/,https://ghproxy.net/,https://gh.api.99988866.xyz/}"

build_download_urls() {
    local raw_path="$1"
    local urls=()
    local mirrors=()

    if [ "${BAMBU_DIRECT:-0}" != "1" ] && [ -n "$GITHUB_MIRRORS" ]; then
        IFS=',' read -ra mirrors <<< "$GITHUB_MIRRORS"
        for m in "${mirrors[@]}"; do
            m="${m%/}"
            urls+=("${m}/${raw_path}")
        done
    fi
    urls+=("$raw_path")
    printf '%s\n' "${urls[@]}"
}

# Color helpers
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info()  { echo -e "${BLUE}[INFO]${NC}  $*"; }
log_ok()   { echo -e "${GREEN}[OK]${NC}    $*"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_err()  { echo -e "${RED}[ERROR]${NC} $*"; }

# ---------------------------------------------------------------------------
# Uninstall
# ---------------------------------------------------------------------------
do_uninstall() {
    log_info "正在卸载 Bambu Studio ..."
    if [ -d "$INSTALL_DIR" ]; then
        rm -rf "$INSTALL_DIR"
        log_ok "已删除 $INSTALL_DIR"
    else
        log_warn "$INSTALL_DIR 不存在"
    fi
    if [ -L "/usr/local/bin/bambu-studio" ]; then
        rm -f /usr/local/bin/bambu-studio
        log_ok "已删除 /usr/local/bin/bambu-studio"
    fi
    if [ -f "/usr/local/bin/bambu-studio" ]; then
        rm -f /usr/local/bin/bambu-studio
        log_ok "已删除 /usr/local/bin/bambu-studio"
    fi
    log_ok "卸载完成"
    exit 0
}

# ---------------------------------------------------------------------------
# Check prerequisites
# ---------------------------------------------------------------------------
check_prereqs() {
    log_info "检查系统环境 ..."

    local missing=()

    for cmd in curl jq tar; do
        if ! command -v "$cmd" &>/dev/null; then
            missing+=("$cmd")
        fi
    done

    if [ ${#missing[@]} -gt 0 ]; then
        log_warn "缺失命令: ${missing[*]}"
        if [ "${SKIP_APT_DEPS:-0}" = "1" ]; then
            log_warn "SKIP_APT_DEPS=1，跳过自动安装"
        elif command -v apt-get &>/dev/null; then
            log_info "apt-get install ${missing[*]} ..."
            sudo apt-get update -qq
            sudo apt-get install -y -qq "${missing[@]}"
        elif command -v dnf &>/dev/null; then
            sudo dnf install -y "${missing[@]}"
        elif command -v yum &>/dev/null; then
            sudo yum install -y "${missing[@]}"
        else
            log_err "无法自动安装依赖: ${missing[*]}"
            exit 1
        fi
    fi

    # Check xvfb (required for headless CLI)
    if ! command -v xvfb-run &>/dev/null; then
        log_warn "xvfb 未安装 (无图形服务器 headless 切片必需)"
        if [ "${SKIP_APT_DEPS:-0}" != "1" ] && command -v apt-get &>/dev/null; then
            sudo apt-get install -y -qq xvfb
        elif [ "${SKIP_APT_DEPS:-0}" != "1" ] && command -v dnf &>/dev/null; then
            sudo dnf install -y xorg-x11-server-Xvfb
        fi
    fi

    # Check and install shared libraries commonly missing for AppImage
    if command -v apt-get &>/dev/null && [ "${SKIP_APT_DEPS:-0}" != "1" ]; then
        local libs=()

        for lib_pair in \
            "libwebkit2gtk-4.0-dev|libwebkit2gtk" \
            "libwebkit2gtk-4.1-dev|libwebkit2gtk" \
            "libavcodec61|libavcodec" \
            "libavformat61|libavformat" \
            "libswscale8|libswscale" \
            "libfuse2|fuse" \
        ; do
            local pkg="${lib_pair%%|*}"
            local label="${lib_pair##*|}"
            if dpkg -s "$pkg" &>/dev/null 2>&1; then
                continue
            fi
            if apt-cache show "$pkg" &>/dev/null 2>&1; then
                libs+=("$pkg")
            else
                log_warn "$label 未安装，且 $pkg 不在当前发行版仓库中 (AppImage 自带依赖可能无法工作)"
            fi
        done

        if [ ${#libs[@]} -gt 0 ]; then
            log_info "安装缺失的共享库: ${libs[*]}"
            sudo apt-get update -qq
            sudo apt-get install -y -qq "${libs[@]}" 2>/dev/null || log_warn "部分库安装失败，请手动检查"
        fi
    fi

    log_ok "系统环境检查完成"
}

# ---------------------------------------------------------------------------
# Fetch latest version
# ---------------------------------------------------------------------------
fetch_version() {
    if [ -n "${BAMBU_VERSION:-}" ]; then
        VERSION="$BAMBU_VERSION"
        log_info "使用指定版本: $VERSION"
        return
    fi

    log_info "获取 Bambu Studio 最新版本号 ..."

    local redirect_url
    redirect_url=$(curl -sI -o /dev/null -w '%{redirect_url}' \
        "https://github.com/bambulab/BambuStudio/releases/latest" 2>/dev/null || true)

    if [ -n "$redirect_url" ]; then
        VERSION="${redirect_url##*/}"
        VERSION="${VERSION#v}"
        VERSION="${VERSION#V}"
    fi

    if [ -z "$VERSION" ] || [ "$VERSION" = "latest" ]; then
        log_info "通过 GitHub API 获取版本 ..."
        local api_url="https://api.github.com/repos/bambulab/BambuStudio/releases/latest"
        local api_opts=(-s)
        if [ -n "${GITHUB_TOKEN:-}" ]; then
            api_opts+=(-H "Authorization: Bearer $GITHUB_TOKEN")
        fi
        local tag_name
        tag_name=$(curl "${api_opts[@]}" "$api_url" | jq -r '.tag_name // empty' 2>/dev/null || true)
        VERSION="${tag_name#v}"
        VERSION="${VERSION#V}"
    fi

    if [ -z "$VERSION" ]; then
        log_err "无法自动获取最新版本号，请手动设置: BAMBU_VERSION=02.06.00.51 bash install_bambu_slicer.sh"
        exit 1
    fi

    log_ok "最新版本: v$VERSION"
}

# ---------------------------------------------------------------------------
# Download AppImage
# ---------------------------------------------------------------------------
download_appimage() {
    local raw_base="https://github.com/bambulab/BambuStudio/releases/download"

    local url_paths=()
    url_paths+=("/v${VERSION}/Bambu_Studio_linux_${DISTRO}-v${VERSION}.AppImage")
    url_paths+=("/V${VERSION}/Bambu_Studio_linux_${DISTRO}-V${VERSION}.AppImage")

    if [ "$DISTRO" = "fedora" ]; then
        url_paths+=("/v${VERSION}/Bambu_Studio_linux_ubuntu-v${VERSION}.AppImage")
    elif [ "$DISTRO" = "ubuntu" ]; then
        url_paths+=("/v${VERSION}/Bambu_Studio_linux_fedora-v${VERSION}.AppImage")
    fi

    APPIMAGE_NAME="Bambu_Studio_linux_${DISTRO}-v${VERSION}.AppImage"

    local all_urls=()
    for path in "${url_paths[@]}"; do
        local expanded
        expanded="$(build_download_urls "${raw_base}${path}")"
        while IFS= read -r u; do
            [ -n "$u" ] && all_urls+=("$u")
        done <<< "$expanded"
    done

    for url in "${all_urls[@]}"; do
        log_info "尝试下载: $url"
        local http_code
        http_code=$(curl -sL -o /dev/null -w '%{http_code}' --connect-timeout 8 "$url" 2>/dev/null || true)
        if [ "$http_code" = "200" ] || [ "$http_code" = "302" ]; then
            DOWNLOAD_URL="$url"
            break
        fi
        log_warn "HTTP $http_code — 尝试下一个"
    done

    if [ -z "$DOWNLOAD_URL" ]; then
        log_err "无法找到 Bambu Studio v${VERSION} 的下载地址"
        log_err "请手动指定版本: BAMBU_VERSION=xx.xx.xx.xx bash install_bambu_slicer.sh"
        log_err "可用的版本列表: https://github.com/bambulab/BambuStudio/releases"
        exit 1
    fi

    log_info "下载地址: $DOWNLOAD_URL"
    log_info "下载中 (约 120MB)，请稍候 ..."

    sudo mkdir -p "$INSTALL_DIR"

    local tmp_appimage="/tmp/${APPIMAGE_NAME}"
    curl -L --progress-bar -o "$tmp_appimage" "$DOWNLOAD_URL"

    if [ ! -f "$tmp_appimage" ] || [ ! -s "$tmp_appimage" ]; then
        log_err "下载失败或文件为空"
        exit 1
    fi

    local fsize
    fsize=$(stat -c%s "$tmp_appimage" 2>/dev/null || stat -f%z "$tmp_appimage" 2>/dev/null || echo 0)
    if [ "$fsize" -lt 10000000 ]; then
        log_err "下载的文件太小 (${fsize} bytes)，可能不是有效的 AppImage"
        exit 1
    fi

    log_ok "下载完成 ($(( fsize / 1048576 )) MB)"
}

# ---------------------------------------------------------------------------
# Extract and install
# ---------------------------------------------------------------------------
install_from_appimage() {
    local tmp_appimage="/tmp/${APPIMAGE_NAME}"

    log_info "解压 AppImage ..."
    chmod +x "$tmp_appimage"

    EXTRACT_DIR="$(mktemp -d /tmp/bambu-extract-XXXXXX)"

    (
        cd "$EXTRACT_DIR"
        "$tmp_appimage" --appimage-extract >/dev/null 2>&1
    ) || {
        log_err "AppImage 解压失败，请检查依赖: apt install libfuse2"
        rm -rf "$EXTRACT_DIR" "$tmp_appimage"
        exit 1
    }

    log_ok "解压完成"

    log_info "安装到 $INSTALL_DIR ..."
    sudo mkdir -p "$INSTALL_DIR/bin"

    if [ -d "$EXTRACT_DIR/squashfs-root" ]; then
        sudo cp -rf "$EXTRACT_DIR/squashfs-root"/* "$INSTALL_DIR/" 2>/dev/null || true
    fi

    rm -rf "$EXTRACT_DIR" "$tmp_appimage"

    local exe_path=""
    for candidate in \
        "$INSTALL_DIR/bin/bambu-studio" \
        "$INSTALL_DIR/AppRun" \
        "$INSTALL_DIR/bambu-studio" \
        "$INSTALL_DIR/BambuStudio" \
    ; do
        if [ -f "$candidate" ]; then
            exe_path="$candidate"
            break
        fi
    done

    if [ -z "$exe_path" ]; then
        log_err "安装后未找到 bambu-studio 可执行文件"
        log_info "$INSTALL_DIR 内容:"
        ls -la "$INSTALL_DIR/" 2>/dev/null || true
        ls -la "$INSTALL_DIR/bin/" 2>/dev/null || true
        exit 1
    fi

    sudo chmod +x "$exe_path"

    # create wrapper script that handles headless mode
    log_info "创建 wrapper 脚本 ..."
    local wrapper="$INSTALL_DIR/bin/bambu-studio-cli"

    sudo tee "$wrapper" > /dev/null << 'WRAPPER_EOF'
#!/usr/bin/env bash
# Bambu Studio CLI wrapper for headless slicing on servers without X11
# Auto-detects xvfb-run and uses it when DISPLAY is not set.

HERE="$(cd "$(dirname "$0")" && pwd)"
EXE="$HERE/bambu-studio"

if [ ! -f "$EXE" ]; then
    EXE="$(dirname "$HERE")/AppRun"
fi
if [ ! -f "$EXE" ]; then
    echo "ERROR: bambu-studio executable not found" >&2
    exit 1
fi

if [ -z "${DISPLAY:-}" ] && command -v xvfb-run &>/dev/null; then
    exec xvfb-run -a "$EXE" "$@"
else
    exec "$EXE" "$@"
fi
WRAPPER_EOF

    sudo chmod +x "$wrapper"

    local real_exe="$wrapper"
    if [ "${SKIP_SYMLINK:-0}" != "1" ]; then
        sudo ln -sf "$wrapper" /usr/local/bin/bambu-studio 2>/dev/null || {
            log_warn "无法创建 /usr/local/bin/bambu-studio 软链接 (可能需要 sudo)"
            log_info "请手动添加到 PATH: export PATH=\"$INSTALL_DIR/bin:\$PATH\""
        }
    fi

    log_ok "安装完成: $real_exe"
}

# ---------------------------------------------------------------------------
# Install and setup profiles
# ---------------------------------------------------------------------------
setup_profiles() {
    local profile_src="${PROFILE_SRC:-}"

    if [ -z "$profile_src" ]; then
        local script_dir
        script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
        profile_src="$script_dir/../profiles/bambu"
    fi

    if [ ! -d "$profile_src" ]; then
        log_warn "未找到 profiles 源目录: $profile_src"
        log_info "将使用 parser/slicer.py 内置的默认参数"
        return
    fi

    local profile_dst="$INSTALL_DIR/profiles"
    mkdir -p "$profile_dst"
    cp -f "$profile_src"/*.json "$profile_dst/" 2>/dev/null || true
    log_ok "Profiles 已安装到: $profile_dst"
}

# ---------------------------------------------------------------------------
# Verify installation
# ---------------------------------------------------------------------------
verify_installation() {
    log_info "验证安装 ..."

    local exe="${1:-bambu-studio}"

    if ! command -v "$exe" &>/dev/null; then
        if [ -f "$INSTALL_DIR/bin/bambu-studio-cli" ]; then
            exe="$INSTALL_DIR/bin/bambu-studio-cli"
        else
            log_err "bambu-studio 命令不可用"
            log_info "请手动添加: export PATH=\"$INSTALL_DIR/bin:\$PATH\""
            return 1
        fi
    fi

    log_info "运行: $exe --help"
    local help_output
    help_output=$("$exe" --help 2>&1) || true

    if echo "$help_output" | grep -qE "(BambuStudio|--slice|--export-3mf)"; then
        log_ok "Bambu Studio CLI 工作正常"
    else
        log_warn "--help 输出不包含预期的 CLI 参数"
        log_info "输出内容 (前500字符):"
        echo "$help_output" | head -c 500
        echo
    fi

    echo ""
    echo "============================================================"
    echo -e "  ${GREEN}Bambu Studio CLI 安装完成${NC}"
    echo "============================================================"
    echo ""
    echo "  可执行文件: $exe"
    echo "  安装目录:   $INSTALL_DIR"
    echo "  版本:       v$VERSION"
    echo ""
    echo "  快速测试:"
    echo "    bambu-studio --help"
    echo "    bambu-studio --slice 1 --load-settings machine.json --export-3mf out.3mf model.stl"
    echo ""
    echo "  卸载:"
    echo "    bash install_bambu_slicer.sh --uninstall"
    echo ""
    echo "============================================================"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    if [ "${1:-}" = "--uninstall" ] || [ "${1:-}" = "-u" ]; then
        do_uninstall
    fi

    if [ "$(id -u)" -eq 0 ]; then
        log_warn "当前以 root 运行，建议以普通用户运行 (需要 sudo 时会自动提示)"
    fi

    echo ""
    echo "============================================================"
    echo "  Bambu Studio CLI 自动安装脚本"
    echo "  目标系统: ${DISTRO} (Linux)"
    echo "  安装路径: ${INSTALL_DIR}"
    echo "============================================================"
    echo ""

    if [ -f "$INSTALL_DIR/bin/bambu-studio-cli" ] || [ -f "$INSTALL_DIR/bin/bambu-studio" ]; then
        log_warn "检测到已有安装: $INSTALL_DIR"
        read -r -p "  是否覆盖安装? [y/N] " yn
        if [ "${yn,,}" != "y" ] && [ "${yn,,}" != "yes" ]; then
            log_info "已取消"
            exit 0
        fi
        sudo rm -rf "$INSTALL_DIR"
    fi

    check_prereqs
    fetch_version
    download_appimage
    install_from_appimage
    setup_profiles
    verify_installation
}

main "$@"
