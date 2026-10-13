#!/usr/bin/env sh
# 开发环境一键启动：帧源（可选合成）+ 守护进程
#   ./scripts/dev-up.sh                 使用 config.toml 的 display（默认 :99 合成源）
#   ./scripts/dev-up.sh --real          真实桌面（用 $DISPLAY）
# 日志：~/backseat-data/run.log（守护进程）与 /dev/null（视频源）
set -eu
cd "$(dirname "$0")/.."
DATA="$HOME/backseat-data"
LOG="$DATA/run.log"
mkdir -p "$DATA"

MODE="${1:-synthetic}"
if [ "$MODE" = "--real" ] || [ "$MODE" = "real" ]; then
  [ -n "${DISPLAY:-}" ] || { echo "需要 X11 会话（DISPLAY 未设置）"; exit 1; }
  echo "帧源：真实桌面 $DISPLAY（config.toml 的 display 应删除或设为空）"
else
  if ! pgrep -x Xvfb >/dev/null; then
    setsid nohup Xvfb :99 -screen 0 1920x1080x24 -nolisten tcp >/dev/null 2>&1 < /dev/null &
    sleep 2
  fi
  if ! pgrep -x mpv >/dev/null; then
    export DISPLAY=:99
    setsid nohup mpv --fullscreen --loop-playlist --mute=yes --no-terminal \
      --vo=x11 --gpu-context=x11egl "$HOME"/Videos/backseat-data/*.mp4 \
      >/dev/null 2>&1 < /dev/null &
    sleep 2
  fi
  echo "帧源：合成（Xvfb :99 + mpv）"
fi

if pgrep -f "python3 -m backseat --data-dir" >/dev/null; then
  echo "守护进程已在运行"
else
  [ -f "$LOG" ] && { tail -c 500000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"; }
  setsid nohup python3 -m backseat --data-dir "$DATA" >> "$LOG" 2>&1 < /dev/null &
  sleep 3
  echo "守护进程已启动（日志 $LOG）"
fi
