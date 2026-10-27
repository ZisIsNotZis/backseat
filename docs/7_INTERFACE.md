# 7_INTERFACE — 接口与数据契约

这一层是接口层：核心只认的组件与它们之间交换的数据形状——数据结构、字段、枚举、装配顺序、可观测事实闭集。前提是实现遵守 [6_ARCHITECTURE.md](6_ARCHITECTURE.md) 的链路；这里只回答"核心与插件之间交换什么"，不解释"为什么这样设计"（见 [6_ARCHITECTURE.md](6_ARCHITECTURE.md)），不写写入规则的判定细节（见 [8_DETAIL.md](8_DETAIL.md)），不写阈值（见 [0_ROADMAP.md](0_ROADMAP.md)），不写命令（见 [9_IMPLEMENTATION.md](9_IMPLEMENTATION.md)）。

# EXPLICIT

本层没有用户的直接叙述或拍板；接口与数据契约全部由项目推导，正文集中在下方 INFERRED 分区；推断不得写进本区。

# CONFIRMED

本层没有【已确认】事项。

# INFERRED

## 表结构公理

- 表描述引擎本体——观察 -> 理解 -> 表达/执行。
- 插件与输入输出手段不是表，是字段值（`observations.sensor` / `expressions.channel`）。
- obs 单表承载一切传感器（screen/atspi/dbus/…），expressions 单表承载一切出口（danmaku/notification/avatar/…）。
- 见证层只输出语义（content/mood/salience），介质呈现（颜色/字号）是出口插件对 mood/salience 的映射——弹幕不是词汇，是翻译结果。
- `observations` 增 `display` 列（多显示器预留，V2 只抓主屏）。

## Mini（见证层输出，small 消息）

严格 JSON，单对象，无包裹文字：

```json
{"type": "anchor|diff",
 "describe": "…（type=anchor 必填，场景整体）",
 "diff": "…（type=diff 必填，相对上次变化）",
 "intent": "行为动词短语（必填）",
 "candidate_project": "新项目首见时的待检词（可选）",
 "importance": 0.0}
```

- 纯事实层：无 roast 字段（风格归人格层，见 [6_ARCHITECTURE.md](6_ARCHITECTURE.md)）。
- 认知边界：深潜前不定业务性质；`candidate_project` 是深潜触发（调度器枚举①）的输入。
- `importance` 是事件重要度（驱动 pin/深潜），与 `expression.salience`（发言重要度）两码事；同名两义已拆分。

## Expression（表达与行为，expressions 行）

`kind ∈ {speak, propose, act}`；`channel` = 出口插件 id（danmaku|notification|cli|…）；`mood` 闭集：`neutral focused happy frustrated mocking guilty anxious excited shocked tired`；`salience` 0~1 连续值。
`status ∈ {sent, shown, accepted, rejected, done, failed}`（sent→shown 由出口回执推进；propose/act 走 accepted/rejected/done；出口写失败记 `failed`）。

回执机制（弹幕出口）：引擎逐条回执（落位成功=`shown`、无空余 lane=`skipped`），出口按 id 关联并在上界内等待——只有 `shown` 才算已渲染。
`skipped`/超时/旧引擎无回执映射为 `sent`（已投递未确认渲染），即回执不可得时不谎报 shown。
出口插件声明支持集，未知 mood 降级 neutral；介质翻译（颜色/字号/打断等级）是插件对 mood/salience 的映射——弹幕不是词汇，是翻译结果。

## M0″ 外部材料（Material）与决策日志

一条表达只能基于外部材料。材料对象在一次尝试内交换：

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | TEXT | 本次尝试内唯一（`m1`…`mN`）；模型只能引用它 |
| `kind` | TEXT | `repo_readme` / `repo_doc` / `git_log` / `git_status` / `git_diff` / `session` |
| `project` | TEXT | 材料所属项目路径；引用必须与题面项目一致 |
| `locator` | TEXT | 事后可复查的位置：`相对路径#Lx-y`、`git:<命令>` 或 `会话文件@UTC时间戳` |
| `fetched_at` | TEXT | 取到的时刻，UTC ISO |
| `excerpt` | TEXT | 取到的正文（有上限） |
| `sha256` | TEXT | `excerpt` 的 utf-8 sha256，用于判官复算 |

落库时表达仍只住 `expressions`：`source_refs` 每个引用一项
`{"table":"materials","id","locator","fetched_at","sha256","project"}`；有触发观察时再加
`{"table":"observations","id"}`。`meta` 记 `thesis`、`material_count`、`gate`、`project`、`title`。
不新增表。

每个尝试（说与沉默）向 `<data_dir>/grounding/decisions-YYYY-MM-DD.jsonl` 追加一个 JSON 对象：

| 字段 | 说明 |
|---|---|
| `ts` / `ts_wall` | UTC ISO / 墙钟秒 |
| `moment_id` | 抽样与人工标签的稳定 id（会话来源为 `文件@时间戳`，可为 null） |
| `source` | `cli` / `observation` / `session` |
| `outcome` | `speak` / `silence` |
| `reason` | `ok`，或沉默原因闭集：`no_project` `no_material` `model_disabled` `model_error` `parse_error` `model_silence` `no_thesis` `uncited` `unknown_citation` `cross_project` `thesis_project_mismatch` `empty_content` |
| `title` / `project` | 触发与解析出的项目（解析不出为 null） |
| `thesis` | 模型题面（一句话）；沉默可为 null |
| `materials` | 本次取到的**全部**材料（字段同上，含 locator/fetched_at/sha256） |
| `content` / `citations` | 评论正文与所引材料 id 列表 |
| `source_obs` / `expression_id` | 触发观察 id / 落库的 expressions id（沉默为 null） |

判官只读这个日志：引用 id 必须在本条 `materials` 里、项目一致、`excerpt` 与其 `sha256` 自洽、locator 文件仍在；
带行号的仓库 locator 按切片重算哈希，文件在 `fetched_at` 之后被改过记为 `stale`（取时快照仍成立，不算伪造），
`git:` 这类动态 locator 不可复算时只查可用性。阈值与退出码归 [0_ROADMAP.md](0_ROADMAP.md)
与 [9_IMPLEMENTATION.md](9_IMPLEMENTATION.md)。

## 五个插件契约（核心只认这五个）

```
Sensor(t)  -> Event[]          事件带双轨时间戳，(kind,key,窗) 总线去重
Outlet(expr) -> status回执      推进 expressions.status
Persona    -> Expression草稿    独立冻结前缀，订阅事实+KB+USER_MODEL；引擎闸门在其后
Model(messages) -> text        OpenAI 兼容；usage: {in,out,cached,latency_ms}
Store                       SQLite；引擎是唯一写入者（侦探只读）
```

人格层公理：事实归引擎，风格归人格。见证层/压缩层/侦探层只产出事实（diff/意图/档案），永远不吐槽；人格是插件，订阅下层事实+KB命中+USER_MODEL，独立装配自己的冻结前缀（人设 prompt），产出表达草稿；引擎闸门（节流/去重/沉默）施加于草稿之后。N 个人格 = N 个插件实例 = N 种口吻，互不污染核心。

装配靠配置文件。跨平台 = 换 Sensor 适配器（Linux: x11grab+ATSPI；Win: UIA；mac: AX），核心零改动。

## KB（唤起记忆，kb 行）

`keys`（定位符 json 数组）+ `desc` + `kind ∈ {fact, reminder, contact, proposal}` + `special ∈ {!always, !at=…}` + `open` + `last_shown`。
新 mini 文本命中任意 key → 该行注入 [KB命中行]；`!always` 周期重放；`!at=时间` 到点提醒。

## Context 组装顺序（缓存契约）

```
[system 冻结（含记忆引擎说明段）] [USER_MODEL] [BIGs] [MEDIUMs] [SMALLs 只增]
→ [KB命中行] → [expressions 尾部] → [本轮: now+mode, ≤1 图]
```

时钟放最末；缓存失效只发生在合并边界。逐调用计量落 `metrics` 表；全量对话落 `~/backseat-data/trajectory/YYYY-MM-DD.jsonl`。为什么顺序如此（缓存只在合并边界断、时钟放最后）见 [6_ARCHITECTURE.md](6_ARCHITECTURE.md)。

早一版的设计里这段前缀还有一个 `[schema 冻结]` 段：当前契约与代码都没有它（`backseat/witness.py` 只注入所列各段），**以本段为准**；它消失不改变任何字段。

## L0 可观测事实闭集

传感器只能落进下面这个闭集；不在闭集里的东西要么以原始帧/快照进 L0，要么不进 L0。

新增一项须同时改 `backseat/bus.py` 的 EventKind 与本节。

### 已实现

| 传感器 | 事实（kind） | 接入状态 | 说明 |
|---|---|---|---|
| screen | `window_switch` `workspace_switch` `fullscreen_toggle` `idle_resume` `display_change` | 已实现（`backseat/bus.py` 的 EventKind） | 大事件封闭枚举；锁屏不是事件——静止画面由 pHash 门控自处理；睡眠唤醒=墙钟跳变，天然会话边界 |

### 计划（未实现）

下表是已规划、但当前 `backseat/bus.py` 的 EventKind 没有、L0 也产不出的 kind；接入时必须同时改 `backseat/bus.py` 与本节。

| 传感器 | 事实（kind） | 接入状态 | 说明 |
|---|---|---|---|
| atspi | `atspi.text_diff` `atspi.tree_diff` | 未实现 | 无障碍树的文本差分与结构差分，文字事实优先于 VLM 转写 |
| fs | `fs.create` `fs.modify` `fs.rename` `fs.delete` | 未实现 | 只记路径与哈希，不记文件内容 |
| git | `git.head` `git.index` `git.worktree` | 未实现 | 仓库状态变化，供"AI coding 爆发"与改动归属 |
| proc | `proc.start` `proc.exit` | 未实现 | 进程起落，供会话与工具识别 |
| input | `input.activity` | 未实现 | 只记活跃度（有无输入、距上次输入的时间），不记按键内容 |
| audio | `audio.transcript` | 未实现 | 转写文本与说话人区间，接入状态见 [0_ROADMAP.md](0_ROADMAP.md) 的通道 D |

L0 只产出可观测事实；"用户回来了"、"用户在纠正"这类高层事件是语义层推断，永远不会作为传感器输入进入 L0。
每个 Observation 带墙钟与单调钟双轨时间戳，并按 (kind, key, 窗) 在总线上去重。

## labels 命名空间契约

新增一张表，需迁移：`backseat/store.py` 当前建了六张表（observations / messages / kb / expressions / metrics / state）；第 7 张是见证层的 `epithets`（称呼册，由 `backseat/witness.py` 建，不归 store 管），所以 labels 是第 8 张。

**labels 不进 M0′**：迁移已获授权，但归属 M1′——通道 B 的判官写出来之前没有读者会用它；M0′ 期间不建该表，通道 B 的判官只报已有事件类型的捕获率，不依赖 labels。
labels 是独立命名空间，独占该表，与 observations / messages / expressions 不共享行，也不共享 id 空间。

| 字段 | 类型 | 说明 |
|---|---|---|
| id | INTEGER PK | 行 id |
| ts_wall | REAL | 标签所指行为的发生时间（墙钟） |
| target | TEXT | 被标注对象的定位：observation id、message id 或时段 |
| project | TEXT | 标签所属项目，用来数"覆盖 ≥2 个项目" |
| kind | TEXT | 标签种类：决策点、纠正、离开、回来、接受、拒绝… |
| value | TEXT | 标签取值 |
| source | TEXT | 标签来源；当前唯一来源是 pi 会话文件 |
| ref | TEXT | 原始出处定位（会话文件路径与偏移），供复核 |
| split | TEXT | 评测切分：train / test / frozen；frozen 即 hard test set |

- 来源是 pi 会话文件（`~/.pi/agent/sessions/`），属文件系统事实：它记录用户与 agent 的真实决策，不经过本系统的采集。
- 标签只当尺子：永不进入传感器输入，也不进入 prefix 装配。
- 用户数据默认留出、不进训练：observations、messages、帧与 labels 默认都不用于训练。

### 用户数据留出规则（仅在未来做模型训练时生效）

- 训练前门槛：≥300 个带标签决策事件【待用户确认（建议值）】、覆盖 ≥2 个项目、≥2 周【待用户确认（建议值）】，才允许切成 train/test。
- 切分按目标变量。
- hard test set 冻结后不得复看，复看即该次评测作废；冻结态由 `split='frozen'` 记录。
- 未满足上述门槛前，任何用户数据都不参与任何训练或调参。

## 语义层证据引用（字段语义）

语义层的每一条都必须能回到原始流；不能回指的条目不是语义，是猜测。

- `messages.obs_ids` 是唯一的引用通道：small 消息写入时由引擎填上它读过的 `observations.id`，读取方只读不改。
- `content` 与 `intent` 里出现的任何可核对断言，都要能在 `obs_ids` 指向的原始观测里找到依据。
- 高层事件（"用户回来了""用户在纠正""用户在放弃"）是语义层推断，必须带证据引用，永不作为传感器输入。
- 推断只向下游流动，不写回 observations；语义条目须带 `obs_ids`。
- medium/big 行自身不写 `obs_ids`：mid 的经 `parent_id` 读时汇总，big 的递归下钻到叶子 small。下钻深度、`seen` 防环与返回的截断标志是算法，见 [8_DETAIL.md](8_DETAIL.md)。
- 下钻后仍无证据时 `degraded=True`，`degraded_reason` 取 `no_leaf_obs`（真孤儿）或 `chain_truncated`（深度上限/成环）；
  有部分帧可回指但链被截断时 `degraded=False` 且 `degraded_reason=chain_truncated`；
  帧被驱逐时 `degraded=True` 且 `degraded_reason=frames_evicted`，它优先于截断原因，不被吞掉。
  不回指必须显式降级，禁止静默空集。

称呼册 `epithets` 表：`form` 主键、不封顶、跨重启仍在，由 `backseat/witness.py` 建。

见证层写入时如何确定断言的基帧、如何点名通行称呼、如何取窗口（切换句/称呼/窗口三条落地规则）属写入规则，见 [8_DETAIL.md](8_DETAIL.md)。回指解析率的度量定义见 [2_GOAL.md](2_GOAL.md)，阈值见 [0_ROADMAP.md](0_ROADMAP.md)，判定命令见 [9_IMPLEMENTATION.md](9_IMPLEMENTATION.md)。
