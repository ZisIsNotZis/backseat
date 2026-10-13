#!/usr/bin/env sh
# 停止开发环境：守护进程 + 视频源（不动 litellm 代理——它由用户会话持有）
set -eu
pkill -f "python3 -m backseat --data-dir" 2>/dev/null || true
pkill -x mpv 2>/dev/null || true
pkill -x Xvfb 2>/dev/null || true
sleep 1
echo "已停止：守护进程 / mpv / Xvfb"
