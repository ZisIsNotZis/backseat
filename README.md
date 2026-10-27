# Backseat 🪑

> 它坐在你人生的后座，看完全程，忍不住评论。等你点头，它接过方向盘。

**Backseat（后座）** 是一个本地优先、零配置的 AI 长期伴侣系统。它通过持续观察你的屏幕与系统信号（本地处理，隐私不出机器），
自主构建对你——行为、习惯、意图、模式——的分层长期记忆，并用弹幕、通知、对话等方式与你互动。
理解是它的本体；弹幕只是它的第一张嘴。

## 为什么

主流 AI 落地的路径是：挖掘痛点 → FDE 深入理解业务 → 定制打磨 → 交付。这条路径贵、慢、门槛高，只服务超级痛点。
Backseat 走相反的路：**先部署，先跑起来，理解自己从数据里长出来**。

判断它是否值得做的依据、不做的边界与证据住 [docs/1_PROBLEM.md](docs/1_PROBLEM.md) 与 [docs/2_GOAL.md](docs/2_GOAL.md)。

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

细节住 [docs/6_ARCHITECTURE.md](docs/6_ARCHITECTURE.md)；数据与接口契约住 [docs/7_INTERFACE.md](docs/7_INTERFACE.md)。

## 安装与运行

系统依赖：python3.12+、ffmpeg、x11-utils（xprop/xdpyinfo）、python3-pyqt5。

```sh
./scripts/install.sh              # venv + systemd user service
python3 -m backseat               # 或手动直跑；数据在 ~/backseat-data/
python3 -m backseat --stats       # 计量汇总（调用/tokens/存储水位）
```

端点环境变量、`--config`、合成帧源、停止与回滚、验证与读数、已知坑：全部住
[docs/9_IMPLEMENTATION.md](docs/9_IMPLEMENTATION.md)。配置见 [config.example.toml](config.example.toml)——所有参数都是弹性预算的导出值
（存储预算、日配额、effort），不是魔法常量。

## 文档在哪

按 `AGENTS.md` 的层级编号住 `docs/`：痛点 [1](docs/1_PROBLEM.md)、目标与度量 [2](docs/2_GOAL.md)、实测约束 [3](docs/3_REQUIREMENT.md)、
用例 [4](docs/4_USECASE.md)、产品 [5](docs/5_PRODUCT.md)、架构 [6](docs/6_ARCHITECTURE.md)、契约 [7](docs/7_INTERFACE.md)、
模块内部规则 [8](docs/8_DETAIL.md)、实现与运维 [9](docs/9_IMPLEMENTATION.md)；里程碑、门槛与当前状态住
[docs/0_ROADMAP.md](docs/0_ROADMAP.md)。贡献前读 [CONTRIBUTING.md](CONTRIBUTING.md)。

## License

AGPL-3.0（弹幕引擎移植自同作者 danmaku 项目，同源同许可）
