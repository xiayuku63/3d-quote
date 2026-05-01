#!/bin/bash
echo "🚀 正在从 GitHub 拉取最新代码..."
git pull origin main

# 如果拉取成功，才执行重启
if [ $? -eq 0 ]; then
    echo "🔄 正在重启 3d-quote 服务..."
    sudo systemctl restart pricer3d
    
    # 检查服务状态，确保启动成功
    if systemctl is-active --quiet pricer3d; then
        echo "✅ 更新并重启成功！服务运行正常。"
    else
        echo "❌ 服务启动失败！请检查日志：journalctl -u 3d-quote -f"
    fi
else
    echo "❌ 代码拉取失败，请检查网络连接或 Git 配置。"
fi
