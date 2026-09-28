# Backseat 🪑

> 它坐在你人生的后座，看完全程，忍不住评论。等你点头，它接过方向盘。

**Backseat（后座）** 是一个本地优先、零配置的 AI 长期伴侣系统。它通过持续观察你的屏幕与系统信号（本地处理，隐私不出机器），自主构建对你——你的行为、习惯、意图、模式——的分层长期记忆，并用弹幕、通知、对话等方式与你互动。理解是它的本体；弹幕只是它的第一张嘴。

## 为什么

主流 AI 落地的路径是：挖掘痛点 → FDE 深入理解业务 → 定制打磨 → 交付。这条路径贵、慢、门槛高，只服务超级痛点。Backseat 走相反的路：**先部署，先跑起来，理解自己从数据里长出来**。

## 架构一瞥

```
传感器插件 ──事件──▶ 记忆引擎（核心） ──输出──▶ 出口插件
屏幕/x11         瞬间→短期→中期→长期      弹幕/通知/人偶/…
                原始→表象→行为→意图→画像
                人格层（风格）∥ 引擎（事实）
```

- **两条正交轴**：时间（瞬间/短/中/长期）× 语义（原始/表象/行为/画像）
- **插件即渠道**：传感器、出口、模型、人格全部可插拔，核心引擎零依赖具体渠道
- **事实归引擎，风格归人格**：见证/压缩只产事实，「弹幕君」人格独立插件
- **本地优先**：原始数据不出机器；LLM 走你自己的 OpenAI 兼容端点

## 安装与运行（Linux/X11）

```sh
# 系统依赖：python3.12+ ffmpeg x11-utils(xprop/xdpyinfo) python3-pyqt5
./scripts/install.sh              # venv + systemd user service
systemctl --user set-environment LITELLM_BASE_URL=http://127.0.0.1:4000
systemctl --user set-environment LITELLM_API_KEY=sk-...
systemctl --user enable --now backseat

# 或手动直跑：
python3 -m backseat               # 数据在 ~/backseat-data/
python3 -m backseat --stats       # 计量汇总（调用/tokens/存储水位）
```

配置见 [config.example.toml](config.example.toml)——所有参数都是弹性预算的导出值（存储预算、日配额、effort），不是魔法常量。

## 状态

Release 2.0 开发中：感知层 + 弹幕出口全链路已跑通（M0-M5），打包收尾（M6）。见 [docs/ROADMAP.md](docs/ROADMAP.md)；数据与接口契约见 [docs/SCHEMA.md](docs/SCHEMA.md)；设计详见 [docs/DESIGN.md](docs/DESIGN.md)；工作流剧本 [docs/WALKTHROUGH.md](docs/WALKTHROUGH.md)。

测试：`python3 tests/test_m0.py && python3 tests/test_m1.py && python3 tests/test_m2.py && python3 tests/test_m3.py && python3 tests/test_m4.py && python3 tests/test_m5.py`

## License

AGPL-3.0（弹幕引擎移植自同作者 danmaku 项目，同源同许可）
