# Backseat Schema v1（M2 固化）

核心语义层 schema。
改动即迁移（ROADMAP M0 门已定稿表结构；本文固化语义契约）。

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

- 纯事实层：无 roast 字段（风格归人格层，DESIGN §10.G4）
- 认知边界：深潜前不定业务性质；`candidate_project` 是深潜触发（调度器枚举①）的输入
- `importance` 是事件重要度（驱动 pin/深潜），与 `expression.salience`（发言重要度）两码事

## Expression（表达与行为，expressions 行）

`kind ∈ {speak, propose, act}`；`channel` = 出口插件 id（danmaku|notification|cli|…）；`mood` 闭集：`neutral focused happy frustrated mocking guilty anxious excited shocked tired`；`salience` 0~1 连续值。
`status ∈ {sent, shown, accepted, rejected, done, failed}`（sent→shown 由出口回执推进；propose/act 走 accepted/rejected/done；出口写失败记 `failed`）。
回执机制（弹幕出口）：引擎逐条回执（落位成功=`shown`、无空余 lane=`skipped`），出口按 id 关联并在上界内等待——只有 `shown` 才算已渲染。
`skipped`/超时/旧引擎无回执映射为 `sent`（已投递未确认渲染），即回执不可得时不谎报 shown。
出口插件声明支持集，未知 mood 降级 neutral；介质翻译（颜色/字号/打断等级）是插件对 mood/salience 的映射——弹幕不是词汇，是翻译结果。

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
| audio | `audio.transcript` | 未实现 | 转写文本与说话人区间，接入状态见 [ROADMAP.md](ROADMAP.md) 的通道 D |

L0 只产出可观测事实；"用户回来了"、"用户在纠正"这类高层事件是语义层推断，永远不会作为传感器输入进入 L0。
每个 Observation 带墙钟与单调钟双轨时间戳，并按 (kind, key, 窗) 在总线上去重。

## 插件契约（核心只认这五个，DESIGN §5）

```
Sensor(t)  -> Event[]          事件带双轨时间戳，(kind,key,窗) 总线去重
Outlet(expr) -> status回执      推进 expressions.status
Persona    -> Expression草稿    独立冻结前缀，订阅事实+KB+USER_MODEL；引擎闸门在其后
Model(messages) -> text        OpenAI 兼容；usage: {in,out,cached,latency_ms}
Store                       SQLite；引擎是唯一写入者（侦探只读）
```

## Context 组装顺序（缓存契约，DESIGN §7）

```
[system 冻结（含记忆引擎说明段）] [USER_MODEL] [BIGs] [MEDIUMs] [SMALLs 只增]
→ [KB命中行] → [expressions 尾部] → [本轮: now+mode, ≤1 图]
```

时钟放最末；缓存失效只发生在合并边界。
逐调用计量落 `metrics` 表；全量对话落 `~/backseat-data/trajectory/YYYY-MM-DD.jsonl`。

## KB（唤起记忆，kb 行）

`keys`（定位符 json 数组）+ `desc` + `kind ∈ {fact, reminder, contact, proposal}` + `special ∈ {!always, !at=…}` + `open` + `last_shown`。
新 mini 文本命中任意 key → 该行注入 [KB命中行]；`!always` 周期重放；`!at=时间` 到点提醒。

## 语义层证据引用

语义层的每一条都必须能回到原始流；不能回指的条目不是语义，是猜测。

- `messages.obs_ids` 是唯一的引用通道：small 消息写入时由引擎填上它读过的 `observations.id`，读取方只读不改。
- `content` 与 `intent` 里出现的任何可核对断言，都要能在 `obs_ids` 指向的原始观测里找到依据。
  见证层写入时的三条落地规则（`backseat/witness.py`）：
  - **切换句**（`从X切至Y`）：被切离的那一端就是这个断言的基帧，写入时并入本行 `obs_ids`
    （模型 `since` 点名的帧优先，缺省取上一条 small 的 `obs_ids`）——过渡的两端同在一行。
  - **通行称呼**（`浅色外套男子`）：只允许来自本行引用的帧。点名不出（`cast` 里没有落在本轮
    图号范围内的项，或本轮根本没有帧）就在正文里删掉，并在 `payload.epithet_refused` 与
    `stats.epithet_refused` 留痕，不沿用上一行。
  - **窗口**：small 行的 `[ts_start, ts_end]` 取该行 `obs_ids` 所指观测 `ts_wall` 的 min/max。
    `observations.id` 与 `ts_wall` 不同序（抓帧多路并发），按 id 取批首批尾会得到倒置窗口与
    落在自窗外的引用；历史库里的 104 条倒置窗口行按已知降级保留，不回填。
- 高层事件（"用户回来了""用户在纠正""用户在放弃"）是语义层推断，必须带证据引用，永不作为传感器输入。
- 推断只向下游流动，不写回 observations；语义条目须带 `obs_ids`，medium/big 的例外见本节下两条（mid 读时汇总、big 递归汇总并显式降级）。
- mid 消息的 `obs_ids` 经 `parent_id` 读时汇总：`backseat/kb.py` 的 flashback 用 `parent_id` 取子行的 `obs_ids` 并求并集，对 mid 有效。
- big 消息同样经 `parent_id` 递归下钻到叶子 small 汇总：`backseat/compressor.py` 合并出来的 medium/big 行不写 `obs_ids`，`backseat/kb.py` 的 `_gather_obs_ids` 对 `obs_ids` 为 NULL 的子行继续下钻（`FLASHBACK_MAX_DEPTH=4`，`seen` 防环），从叶子 small 汇总 `obs_ids`。
- 下钻后仍无证据时 `degraded=True`，`degraded_reason` 取 `no_leaf_obs`（真孤儿）或 `chain_truncated`（深度上限/成环）。
  有部分帧可回指但链被截断时 `degraded=False` 且 `degraded_reason=chain_truncated`。
  帧被驱逐时 `degraded=True` 且 `degraded_reason=frames_evicted`，它优先于截断原因，不被吞掉。
  不回指必须显式降级，禁止静默空集。
- 回指解析率的判定口径住在本文的 [门槛判定程序 D1](#门槛判定程序)，里程碑门槛住在 [ROADMAP.md](ROADMAP.md) 的 M0′。

## labels 命名空间契约

新增第 7 张表，需迁移【待用户确认】：`backseat/store.py` 当前建了六张表（observations / messages / kb / expressions / metrics / state），labels 是第 7 张。
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

## 门槛判定程序

本节的 D1–D8 各占一行，属"一条规则一行"，不按句拆。
判定程序与分子分母住本节；通过阈值住 [ROADMAP.md](ROADMAP.md) 各门槛行，本节只复述以便就地判读。

**D1 各分项各测什么**（口径住本节，彼此正交）：
- **D1 A 测引用可解析且在窗内**：每条 small 行的每个 `obs_ids` 成员，id 在 observations 里存在，
  且该观察的 `ts_wall` 落在本行 `[ts_start, ts_end]` 内。它测引用管道，不测内容真假。
- **D1 B 测断言与帧相符**：独立评审逐行看 `obs_ids` 指向的帧，判"该帧支持该行的语义断言"，
  50 条抽样 ≥95%。它测内容真假，测不到"断言的依据是否就在本行 `obs_ids` 里"。
- 前两条之外，[语义层证据引用](#语义层证据引用) 的三条写入规则管的是"依据在不在行内"：切换句的
  基帧是本行 `obs_ids` 的一员，通行称呼能在本行引用的帧里点名。它与 A/B 正交——B 全过仍可能
  依据落在上一条消息的帧里，而这也不替 B 证明内容为真。它不是门槛：门槛住 ROADMAP，由项目
  自己的节点定，本文只记写入规则与判定口径。

**D1 语义回指率（M0′）**：D1 抽样范围固定为 small 行，不因 big 回指现状而变。从 messages 的 small 行抽样 200 条语义记录。程序：按 obs_ids 解析到 observations。判定 A＝id 存在且其 ts_wall 落在该消息 [ts_start, ts_end] 内；判定 B＝该观察的字段（key/content/ref/meta）确实支持该行的语义断言，由独立评审对 50 条抽样判定。通过＝A 达 100% 且 B ≥95%。

**D2 题集指标（M1′）**：每题一个 exact-match 判定，时间类题目允许 ±60s 容差，其余字符串规范化后相等。准确率＝答对题数/总题数。引用正确率＝通过的 evidence 条数/引用总条数，单条引用通过＝被引单元在库中真实存在且持有引用声明的值、该题 `check_sql` 复算得到与答案相同的值、且该引用的时间落在答案所述区间。"绝对点"是百分点差这一单位；M1′ 的 +15 绝对点＝本法准确率与最强基线准确率之差；被测量指标名固定为"语义状态预测准确率"。

**D3 过度自信率（M2′）**：在系统自报置信 c ≥ 0.8 的题目中答错的比例。若系统不产出逐题置信，该里程碑判不通过，不得用代理量替代。

**D4 撤销率（M4b）**：台账中 status ∈ {reverted, user_edited} 的接管动作数 / 接管动作总数。每次接管动作必须记录 commit SHA 与 diff；用户事后 revert 或改动同一文件即计入。无回归＝接管后既有测试与 check 仍全绿。

**D5 可核对率（M3′）**：提议总数中"按其引用可在 5 秒内由 check_sql 证实或否证"的比例；不可核对者记为不通过。

**D6 跨重启一致率（M1′）**：同一题集在每次进程重启后重跑，答案与重启前相同的题数 / 总题数；≥90%。

**D7 扰动敏感度（M2′）**：对同一批题施加扰动（打乱时序、替换窗口/事件 key、插入无关帧、截断历史）后，答案发生改变的题数 / 受扰动题数；≥80%。

**D8 埋雷捕获率与误杀率（M3′）**：每批固定＝20 条注入缺陷 + 20 条正确提议，误杀率分母恒为 20。向提议集注入 20 条已知缺陷（篡改事实、删证据、引用指错帧）；捕获率＝被判出的注入缺陷数/20，≥80%；误杀率＝被判为缺陷的正确提议数/正确提议总数，≤10%。

M4a 的与 oracle 一致率、M5′ 的先审后发与未审即发检出不在 D1–D8 之内，它们在各自里程碑行里就地定义，判据是全称判断或检出记录，不是抽样比例。
