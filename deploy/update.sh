#!/bin/bash
set -euo pipefail

REMOTE="origin"
BRANCH="main"

echo "🚀 正在从 GitHub 拉取最新代码..."

# 丢弃所有本地未提交的修改（部署服务器不需要保留本地改动）
if [ -n "$(git status --porcelain)" ]; then
    echo "⚠️  检测到本地未提交的修改，正在丢弃..."
    git checkout -- .
    git clean -fd
    echo "✅ 本地修改已丢弃"
fi

# 拉取远程最新
if git pull "$REMOTE" "$BRANCH"; then
    echo "🔄 正在重启 pricer3d 服务..."
    sudo systemctl restart pricer3d

    if systemctl is-active --quiet pricer3d; then
        echo "✅ 更新并重启成功！服务运行正常。"
    else
        echo "❌ 服务启动失败！请检查日志：journalctl -u pricer3d -f"
        exit 1
    fi
else
    echo "❌ 代码拉取失败，请检查网络连接或 Git 配置。"
    exit 1
fi
