#!/usr/bin/env sh
# Backseat 一键安装（M6）：venv + 系统依赖检查 + systemd user service
set -eu
PREFIX="${1:-$HOME/.local}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"

echo "== 系统依赖检查 =="
for c in python3 ffmpeg xprop xdpyinfo; do
  command -v "$c" >/dev/null || { echo "缺少 $c（ubuntu: sudo apt install ffmpeg x11-utils）"; exit 1; }
done
python3 -c 'import PyQt5' 2>/dev/null || { echo "缺少 PyQt5（ubuntu: sudo apt install python3-pyqt5）"; exit 1; }
[ -n "$DISPLAY" ] || { echo "需要在 X11 会话内运行"; exit 1; }

echo "== venv =="
# PyQt5 走系统包（免 PyPI 大件下载），venv 只装本项目
python3 -m venv --system-site-packages "$PREFIX/backseat-venv"
"$PREFIX/backseat-venv/bin/pip" install -e "$REPO" --no-deps --quiet

echo "== systemd user service =="
mkdir -p "$HOME/.config/systemd/user"
cat > "$HOME/.config/systemd/user/backseat.service" <<SVC
[Unit]
Description=Backseat — local-first AI companion (L0-L5)
After=graphical-session.target

[Service]
ExecStart=$PREFIX/backseat-venv/bin/backseat --data-dir %h/backseat-data
Restart=on-failure
Environment=LITELLM_BASE_URL=%h/litellm-base-url
# API key 经环境注入：systemctl --user set-environment LITELLM_API_KEY=...

[Install]
WantedBy=graphical-session.target
SVC
systemctl --user daemon-reload
echo "完成。启动：systemctl --user enable --now backseat"
echo "（LLM 需 LITELLM_BASE_URL / LITELLM_API_KEY 环境变量）"
