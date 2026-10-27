<p align="center"><img src="assets/logo.svg" alt="backseat" width="196" height="56"></p>

[English](README.md) | **简体中文**

# Backseat · 后座

### 它坐在你人生的后座，看完全程，忍不住评论；等你点头，才接过方向盘。

**Backseat 是一个本地优先的 AI 长期伴侣：它把屏幕上发生的事压成分层记忆——瞬间 → 片段 → 行为 → 意图 → 画像——并且只在自己知道你不知道的事时才开口。**

主流 AI 产品的路径是：挖掘痛点 → 派 FDE 深入业务 → 定制打磨 → 交付。这条路径贵、慢，只服务最尖的那几个痛点。Backseat 走相反的路：**先部署，先跑起来，让理解自己从数据里长出来**。

从采集到文字离开本机之前，一切都在本地：帧在自己机器上采样、去重、算哈希，模型请求只发给你自己的 OpenAI 兼容端点。弹幕只是它的第一张嘴，不是产品本体。

## 快速开始

需要 Linux + X11 会话、Python 3.12+、`ffmpeg`、`x11-utils`（`xprop`/`xdpyinfo`）、`python3-pyqt5`。

```sh
git clone https://github.com/ZisIsNotZis/backseat.git && cd backseat
./scripts/install.sh                      # 检查系统依赖、建 venv、写 systemd user service
systemctl --user set-environment LITELLM_BASE_URL=http://127.0.0.1:4000
systemctl --user set-environment LITELLM_API_KEY=sk-...
systemctl --user enable --now backseat     # 或直接跑：python3 -m backseat --data-dir ~/backseat-data
python3 -m backseat --stats                # 调用量、tokens、缓存命中率、帧数、消息层级
```

没有 `LITELLM_BASE_URL`/`LITELLM_API_KEY` 时守护进程照样跑，只是退化成只采集的 L0 模式，启动时会把这件事打印出来。全部可调参数在 `config.example.toml`，复制成 `config.toml`（已 gitignore）即可覆盖；它们是三份弹性预算（`storage_bytes`、`daily_requests`、`effort`）的导出值，不是魔法常量。

```sh
cp config.example.toml config.toml   # 然后按需修改
./scripts/dev-up.sh                  # 合成帧源：Xvfb :99 + mpv 循环，守护进程日志进 ~/backseat-data/run.log
./scripts/dev-up.sh --real           # 或者采真实桌面（$DISPLAY）
./scripts/dev-down.sh                # 停守护进程与视频源（不动你自己的 LiteLLM 代理）
```

## 现在能做什么

- **屏幕传感器（L0）**——X11 抓帧 + 采样节奏控制器（低/中/高三档，由 pHash 动力学 EMA 驱动）、pHash 门控、空帧拦截、约 120 秒的 RAM 环（采集频率 ≠ 处理频率）、封闭枚举的大事件检测，帧落盘并记下 hash/key/bytes。
- **见证层（L1）**——像素→文字的唯一转换点。anchor/diff 双模式与强制重锚、严格 JSON 输出契约、解析失败重试一次后丢弃并记账，逐调用计量 token/成本/缓存/时延，全量写入 `trajectory/` JSONL。
- **证据基，不是感觉**——每一行事实都带着可核对的观察 id；比较句（"从 X 切至 Y"）会把被比的那一帧并进本行的引用；称呼只有在本行点名到具体帧时才能用，点不出来就**整行拒收**，绝不静默沿用旧称呼。
- **记忆金字塔（L2）**——small → medium → big 分层合并（K/M 阈值）、`USER_MODEL` 增量合并、重启后从 bigs + mids + anchor 恢复。
- **唤起记忆（KB + flashback）**——keys 既是检索词也是唤起词、`!always` 周期重放、`!at=YYYY-MM-DD HH:MM` 到点提醒、60 分钟防重，flashback 逐字回放并附帧引用，证据已被驱逐时明确降级，而不是静默返回空。
- **洞察挖掘（无 LLM）**——确定性地找出跨时间的重复、间隔、累积与异常；产品原则是：说话的唯一理由是信息不对称。
- **人格层 + 弹幕出口（M5）**——「弹幕君」是插件，引擎闸门施加在草稿之后（节流、与近期发言重叠 ≥60% 拦截、沉默权）；PyQt5 点击穿透弹幕以子进程跑，stdin JSON-lines 进、每条消息都有回执，所以"shown"是真的渲染了，而不只是发出去了。
- **存储预算与驱逐**——`storage_bytes` 推导留存期；超预算时按 FIFO 驱逐最旧的非豁免帧，并在日志里留下水位。
- **可测**——`metrics` 表、`--stats` 汇总、`--once N` 冒烟跑、在冻结快照上只读的评测尺子、带明确退出码的缺陷普查（0 无缺陷 / 1 有缺陷 / 2 判定人口为空），以及不发模型请求就能按现行写入规则重写历史正文的预演。

## 架构

```
backseat/            引擎本体
  sensors/           x11 探针、屏幕传感器（采样 / 环 / 挑选）、pHash
  witness.py         L1：帧 → 事实（证据基与写入规则只在这一处）
  compressor.py      L2：small→medium→big 合并
  kb.py              唤起记忆、周期重放、flashback
  insight.py         跨时间的确定性观察
  persona.py         人格插件与其输出闸门
  outlets/           danmaku_engine：PyQt5 弹幕层，JSON-lines 进、回执出
  store.py           SQLite：observations / messages / kb / expressions / metrics / state
  bus.py clock.py config.py model.py trajectory.py
evals/               在冻结快照上只读运行的评测尺子（不调模型、不联网）
scripts/             install、dev-up/down、freeze_snapshot、d1_census、rehearse_writes、backfill_epithets、atspi_probe
tests/               14 个互相独立、各自可跑的文件
docs/               0–9 编号的设计记录（痛点 → 目标 → 实测约束 → 用例 → 产品 → 架构 → 契约 → 内部细节 → 实现）
```

整套模型由两条正交轴撑起来：**时间**（瞬间 / 短期 / 中期 / 长期）× **语义**（原始 / 表象 / 行为 / 画像）。传感器与出口是渠道，所以它们是表里的字段值（`sensor`、`channel`）而不是表；核心引擎不依赖任何具体渠道。事实归引擎，风格归人格：见证层与压缩层只产事实，"怎么说"一律住人格插件。表结构描述引擎本体，因此改表就意味着迁移。

先读 [`docs/6_ARCHITECTURE.md`](docs/6_ARCHITECTURE.md)（子系统边界与决策账本）和 [`docs/7_INTERFACE.md`](docs/7_INTERFACE.md)（表与接口契约）。

## 怎么开发

```sh
python3 -m backseat --once 5 --data-dir /tmp/bs-smoke   # 冒烟跑，不需要模型
python3 -m backseat --stats --data-dir /tmp/bs-smoke    # 计量汇总

# 每个测试文件都独立可跑；末尾打印 ALL PASS（N 条断言）即绿
for f in tests/test_*.py; do python3 "$f" >/dev/null || echo "$f FAIL"; done

python3 scripts/freeze_snapshot.py --verify              # 校验冻结快照哈希，不符退出 1
python3 scripts/d1_census.py --db ~/backseat-data/snapshots/<UTC日期>/backseat.db
```

端点自检、驱逐水位、重放复算、已知坑与回滚这些运维细节住 [`docs/9_IMPLEMENTATION.md`](docs/9_IMPLEMENTATION.md)。设计改动要**先**落到文档层（决策进 `docs/6_ARCHITECTURE.md`，契约进 `docs/7_INTERFACE.md`，写入规则进 `docs/8_DETAIL.md`），再改行为。

## 贡献

先读 [`CONTRIBUTING.md`](CONTRIBUTING.md)——它的正文只有四句话。梗概：一个 commit 一个关注点并附实测证据；事实归引擎、风格归人格；确定性算法能决定的，模型不碰；移植代码保持同源同许可。

## 路线图

**已实现（Release 2.0，代码完成）**

- M0 骨架与数据层——事件总线（墙钟 + 单调双轨时间戳）、六张表的 SQLite schema、全部参数由弹性预算推导。
- M1 屏幕传感器——采集、pHash 门控、空帧拦截、事件检测、节奏控制器。
- M2 见证层——anchor/diff、严格 JSON、计量、trajectory。
- M3 记忆金字塔——small/medium/big 合并、`USER_MODEL`、重启恢复。
- M4 唤起记忆——KB 注入、`!always`/`!at`、带降级的 flashback。
- M5 人格层 + 弹幕出口——闸门施加在草稿之后，逐条回执。
- M6 打包——安装脚本、systemd unit、LICENSE、这份 README。

还有几道确认门要的是真实使用而不是更多代码：真跑一天的采集、真实桌面上的 callback 演示、以及人对弹幕频率与口吻的打分。感知层已经到"再多写也增加不了可信度"的位置，所以它转入维护态，靶子换到别处。

**计划中**

- Release 2.1——输入流可插拔（任意帧源：RTSP、图片目录、另一个 display、视频文件，而不只是 `config.display`）、常驻 AT-SPI 传感器与 opportunistic 的 D-Bus、有界的深潜/侦探层、驱逐后仍存活的 pin 帧、本地 VLM 选项。
- Release 2.2——第二张嘴：系统通知出口、CLI 出口（`backseat ask "我这周干了啥"`）、画像导出为 JSON/Markdown。
- Release 3.x——帮助层：从"描述"走到"有据的提议"，再走进一个有界、先 dry-run 的动作面。长期靶子是这样一个伴侣集群：它们的理解合起来超过任何单个人片面的视角——这也是数据模型从 M0 起就保持干净的原因。

里程碑、门槛、阈值与当前状态住 [`docs/0_ROADMAP.md`](docs/0_ROADMAP.md)；它拒绝解决的痛点住 [`docs/1_PROBLEM.md`](docs/1_PROBLEM.md)。

## 许可

AGPL-3.0，版权归 ZisIsNotZis。弹幕引擎移植自同作者的 `danmaku` 项目——同源同许可。
