# 3_REQUIREMENT — 实测约束

本文件记录这台机器、这个人的环境里传感器的实测能力，以及由它推出的硬约束；外加已拍板的隐私/成本两条约束，
与预算→能力的约束侧。
前提：读数来自具体时刻的实测，每条带“读到的时刻 / 读的哪一面 / 复算命令”，命令作为读数的证明保留。
不在这里：通过阈值住 [0_ROADMAP.md](0_ROADMAP.md)；命令与判定步骤、测试策略住 [9_IMPLEMENTATION.md](9_IMPLEMENTATION.md)；
数据结构与字段住 [7_INTERFACE.md](7_INTERFACE.md)；交互与呈现住 [5_PRODUCT.md](5_PRODUCT.md)。

# EXPLICIT

**隐私 Q1（已拍板）**：原始帧永不做遮蔽/打码——不为此复杂化架构。
隐私只发生在文字化边界：L1 提示词含脱敏规则（“转写/描述文字中若出现密码等敏感内容，以▇代替”）。
提示词方案，非系统机制。

**成本 Q2（已拍板）**：V2 开发期不设上限，逐帧计量仅做统计；效果验证后再定预算算法。

# CONFIRMED

（本层暂无独立条目：实测读数归 INFERRED，拍板项归 EXPLICIT。）

# INFERRED

## 传感器实际给了什么（读数，不是设计意图）

本段只放**这台机器上、这个人的环境里**传感器的实测能力，每条带三样：读到的时刻、读的哪一面、复算命令。
设计意图与机制见 [6_ARCHITECTURE.md](6_ARCHITECTURE.md)，里程碑见 [0_ROADMAP.md](0_ROADMAP.md)；这里回答的是“能不能拿到”。

**一句话现状**：这台机器上唯一在用的语义源是屏幕像素；AT-SPI 对这个人实际在用的终端（Ghostty）**不提供任何文本**，
只提供窗口/标签标题与几何。所以屏幕只能当**触发器**，当不了评论的内容来源——内容要么来自外部材料，要么没有。

### 1. Ghostty 终端：AT-SPI 没有任何文本（读于 2026-10-23T13:23:46Z，a11y 总线 `unix:path=/run/user/1000/at-spi/bus_1`；同一读数首次取得约 13:0x Z，两次一致）

| 读什么 | 值 |
|---|---|
| 应用 | `ghostty` 1.3.1，pid 4019，a11y 上 `:1.22` |
| 节点数 | 25 |
| 实现的接口 | `Accessible` 25、`Component` 24、`Action` 23、`Application` 1 |
| **`org.a11y.atspi.Text`** | **0 个节点**（⇒ 没有文本、没有 `CharacterCount`、没有 `GetText`） |
| `org.a11y.atspi.Selection` / `Hypertext` / `Collection` | 0 个节点（⇒ 没有选区 API、没有子串检索） |
| 能读到的 | 窗口/标签标题（`vibe · On`）、几何（`Component.GetExtents` → `(0, 0, 2560, 1440)`）、叶子上的 2 个 `Action` |

复算：

```sh
python3 scripts/atspi_probe.py                 # 列出 a11y 总线上的应用（名字 + bus 名）
python3 scripts/atspi_probe.py ghostty         # 25 nodes；ifaces 里没有 Text
```

结论按问题的三问拆开：**正文？没有。滚动缓冲？没有。选区？没有。** 终端里“用户在做什么”的语义，
在 Ghostty 上只能从像素拿（也就是现有 screen 通道的 VLM）；AT-SPI 只剩触发信息——哪个窗口、哪个标签、
什么时候换、占多大。

### 2. 对照：同一会话里的 VTE 终端**有**文本（说明这不是“AT-SPI 坏了”）

| 读什么 | `gnome-terminal-server`（`:1.17438`，VTE） |
|---|---|
| 节点数 | 90 |
| 接口 | `Accessible` 90、`Collection` 90、`Socket` 90、`Component` 89、`Action` 57、`Selection` 41、`Image` 17、`Text` 3、`Hypertext` 2、`Value` 1 |
| `Text` 节点 | 3 个；`role=terminal` 的节点 `CharacterCount=1673`，`GetText` 返回可见屏文本；`Selection` 接口在（读时 `NSelections=0`） |

复算：`python3 scripts/atspi_probe.py gnome-terminal-server --text-len`（只给数字与节点名，不打印正文）。

所以这是**终端程序的选择**，不是平台能力的极限：同一个 AT-SPI 会话里，VTE 把可见屏文本发布出来，
Ghostty 一个字节都不发。这条差别决定了产品能不能“读终端”：对这个人不能。

### 3. 对设计的硬约束（由上面两条推出）

1. **屏幕 = 触发器**：像素能回答“是不是在同一个终端里打字”“换没换窗口/标签”“画面变没变”；它答不了“这个人到底在做什么项目里的哪一步”。
2. **语义 = 要么外部材料，要么没有**：要说出有质量的话，得按触发拿到的题面（窗口标题、可见文本片段、上下文）去外面取材料（文档、仓库、公开资料），而不是对着一屏终端猜。
3. **“接管”在这台机器上不可达**：没有可读文本 ⇒ 读不出他要做什么；没有可写接口 ⇒ 也没有一个安全的动作面。任何“我能替你做”的说法都是空头支票。

### 4. 过期条件（什么时候重测）

- Ghostty 升级（1.3.1 → 更高）或它的 a11y 相关 issue 落地时，第 1 节重测一次；读数改成新的时刻与节点数。
- 换终端程序（kitty / alacritty / wezterm / VTE 系）时，第 1 节换成那个程序重测——**不要**把 VTE 的结论套到别的终端上。
- 只测过 X11 会话（`DISPLAY=:1`）；Wayland 下 GTK4 的 a11y 走另一条路，未测。

### 5. 其他传感器的现状

- **screen**：唯一在跑的通道。库里 43,026 条 observation 全是 `screen/screen.notable`（读数与命令见 [1_PROBLEM.md](1_PROBLEM.md) 的证据节）。
- **input / proc / fs / git / AT-SPI 事件流**：未接入。接入它们的意义要按第 3 节重估——`proc`/`git`/`fs` 能给出“在哪个仓库、哪条命令在跑”这类结构化事实，恰好是终端文本拿不到的那部分；AT-SPI 事件流对 Ghostty 只剩窗口级事件。

## 预算→能力（约束侧）

所有运行参数都不是常量，而是用户预算的函数；用户设预算，引擎反推参数，实时重算不重启。
这就是“所有策略都有柔性”的落地形式：没有魔法数，只有预算的导出值。

- `storage_bytes`（5G～200G）：约束 L0 留存天数、全分辨率副本配额与驱逐的激进程度。
- `daily_requests`（可选，不设＝不限）：约束 L1 cadence、pHash 门控的收紧程度、coalescing 窗口。
- `effort`：约束 anchor 频率、K/M 压缩粒度、侦探层的开与关。

映射器先实现为阶梯函数（有极），接口按连续值设计（无极）。具体映射表与代码位置住 [6_ARCHITECTURE.md](6_ARCHITECTURE.md)。
