# 8_DETAIL — 模块内部算法与规则

这一层写引擎内部"一行是怎么写对的"：见证层的证据写入三规则、证据如何顺 `parent_id` 向下回指、采样控制器与帧挑选器、压缩与折叠、磁盘满时的驱逐、会话边界怎么推导。它以 [7_INTERFACE.md](7_INTERFACE.md) 的字段契约为前提——字段叫什么、什么类型、语义是什么在那边，这里只写**产生这些字段取值的算法**。不在本层：门槛与度量（[2_GOAL.md](2_GOAL.md)、[0_ROADMAP.md](0_ROADMAP.md)）、命令与运行步骤（[9_IMPLEMENTATION.md](9_IMPLEMENTATION.md)）、预算怎么映射成参数（[6_ARCHITECTURE.md](6_ARCHITECTURE.md)）、表结构公理与策略（[6_ARCHITECTURE.md](6_ARCHITECTURE.md)）。

# EXPLICIT

本层暂无正文。模块内部规则没有一条带"用户自述""用户拍板""已确认"标记——它们是上一任 agent 的设计结论与 grill 轮次的决策，按总图规则全部落在下面的 INFERRED 区。（用户的拍板条目在 [3_REQUIREMENT.md](3_REQUIREMENT.md) 与 [0_ROADMAP.md](0_ROADMAP.md)，不在本层。）

# CONFIRMED

本层暂无正文，原因同上。

# INFERRED

## 证据引用：本层只讲算法

语义层的每一条都必须能回到原始流；不能回指的条目不是语义，是猜测。引用通道（`messages.obs_ids`）、可核对断言的要求、"推断只向下游流动"与"高层事件永不作为传感器输入"是**契约**，正文在 [7_INTERFACE.md](7_INTERFACE.md)；本层下面各节只写产生和读取这些引用的算法。

## 见证层写入的三条规则

见证层是像素→文字的唯一转换点（[backseat/witness.py](../backseat/witness.py) 是证据基与写入规则唯一的地方）。一行写下的每个可核对断言都要在自己的 `obs_ids` 里找到依据，所以写入时做三件事，而不是只挂本轮帧。

### 1. 切换句：基帧取最近一帧，一步、不累积

`从X切至Y` 这类对比/过渡词面出现时，被切离（被比）的那一端就是本行断言的**基帧**，写入时并入本行 `obs_ids`——过渡的两端同在一行。

- 取法：模型 `since` 点名的帧优先（按时间就近匹配）；缺省取**上一条 small 行里 `ts_wall` 最新的那一帧**（即离本行最近的一个观察）。
- 只取一帧：取上一条的整行 `obs_ids` 会在连续对比段里逐行累积，窗口随之往前延伸同样跨度。端点始终是一步，只看模型点名的 `since` 与本库已有行，不做深度搜索。
- 只有内容确实写了对比/过渡词面才加基帧；没有过渡句就没有第二个端点，不加，以免把"上一条"无差别塞进每一行。上一条 small 行一行都没有时返回空：没有前帧可指，就不假装有。
- 对比/过渡词面是**封闭表**（`从X切至Y`、`切换为`、`相较上一帧`、`仍为`…），写死在 [backseat/witness.py](../backseat/witness.py)，不做成可配置——词面宽一档，误报就多一档。
- 基帧身份另记进 `payload.switch_base`，使"依据在不在行内"事后可机械判定（字段语义见 [7_INTERFACE.md](7_INTERFACE.md)，判定口径见 [2_GOAL.md](2_GOAL.md)）。

### 2. 通行称呼与称呼册

`浅色外套男子` 这类通行称呼**只允许来自本行引用的帧**。能留下的只有两种情形：

- `cast` 点名了本轮第 n 张图（图号从 1 起，1≤n≤本轮图数）；
- 册里没有这个词面，且本轮至少有一张图——首次出现只可能来自本帧。`n_frames=0` 时任何词面都没有依据（点名也验不了），整行拒收，**不算**首次出现的例外。

册里记过的词面、本行又写出来而没有点名 → **整行不落库**，正文一个字节不改。不做删名字的文本手术：名字的边界在文本里不可知——词面是名词（男子/女子/…）前连续汉字的后缀变体，最长变体会把邻词与 `切换为` 这类标记词一起吞掉。留痕在 `stats.epithet_refused` 与日志的「整行拒收」一行；行不存在，所以 `payload` 里没有 `epithet_refused` 这个键。

**归一化同源**：写入端与回填端共用同一套词面生成（`backseat.witness.epithet_forms`，名词前 2–6 字后缀变体、去「的」、长→短）与同一份表结构（`EPITHET_SCHEMA`），不另写一套。

**册子**：库里的 `epithets` 表（form 主键、不封顶、跨重启仍在，由 `backseat/witness.py` 建，不归 store 管；表位置见 [7_INTERFACE.md](7_INTERFACE.md)）。写入前先查再记——本行自己写下的词面不算"之前见过"；被拒的词面也照样入册，所以隔几行再写的同一个称呼照样被拒，"被忘掉"这种情况不存在。封顶会静默沿用，故不封顶；每行只按本行的几个词面做主键查询/插入，不随册子变大变慢。

**回填幂等**：被沿用的判据是**跨全库**比，历史行写下时册子可能还不存在，所以写入前由 [回填器](../scripts/backfill_epithets.py) 拿历史回填（运行步骤见 [9_IMPLEMENTATION.md](9_IMPLEMENTATION.md)）。回填只插不删，`ON CONFLICT(form) DO UPDATE SET last_wall=MAX(last_wall, excluded)`，跑两次库里内容一样（第二次不新增词面，只把 `last_wall` 推到历史的真实最晚）。

保留下来的点名记进 `payload.cast`（"这个称呼点了哪一帧"），判"静默沿用"时用它。

### 3. 窗口：取被引用观测 `ts_wall` 的 min/max

small 行的 `[ts_start, ts_end]` 取该行 `obs_ids` 所指观测 `ts_wall` 的 min/max。`observations.id` 与 `ts_wall` 不同序（抓帧多路并发），按 id 取批首批尾会得到**倒置窗口**与落在自窗之外的引用。

历史库的已知降级（按已知降级保留，不回填）：这类行 104 条（引用 232 / 27,012 条落在自窗之外），其中窗口倒置的 12 条。两个数分开数，不合成一个（判定口径见 [2_GOAL.md](2_GOAL.md)，命令见 [9_IMPLEMENTATION.md](9_IMPLEMENTATION.md)）。

## 证据下钻：`_gather_obs_ids` 的深度 4 与 `seen` 防环

压缩合并出的 medium/big 行不写 `obs_ids`，所以读 big 的证据必须顺 `parent_id` 递归下钻到叶子 small 汇总（[backseat/kb.py](../backseat/kb.py)）：

- 子行自带 `obs_ids` 就用；为空则继续下钻。mid 的 `obs_ids` 经 `parent_id` 读时汇总，对 mid 有效。
- `FLASHBACK_MAX_DEPTH=4`：big→medium→small 只需 2 层，留余量并截断异常深链/环。
- `seen` 是路径上已访问的 message id 集（初值为起点自己），`parent_id` 回指已访问行即成环 → 截断。
- 返回 `(obs_ids, truncated)`；被深度上限或成环挡住的子行把 `truncated` 置真。
- 降级（不静默空集）：下钻后仍无证据 `degraded=True`，`degraded_reason` 取 `no_leaf_obs`（真孤儿）或 `chain_truncated`（深度上限/成环）；**有部分帧可回指但链被截断**时 `degraded=False` 且 `degraded_reason=chain_truncated`；帧被驱逐时 `degraded=True` 且 `degraded_reason=frames_evicted`，它优先于截断原因，不被吞掉。
- 字段语义见 [7_INTERFACE.md](7_INTERFACE.md)；回指解析率的判定口径与门槛分别住 [2_GOAL.md](2_GOAL.md) 与 [0_ROADMAP.md](0_ROADMAP.md)。

## 采样控制器与帧挑选器

采集频率 ≠ 处理频率（[backseat/sensors/screen.py](../backseat/sensors/screen.py)）。

**采样节奏控制器**：采样率 = 场景动力学的函数（pHash 差分 EMA）。低→60s 退避 / 中→20s 默认 / 高→2s＋批帧 / 大事件立即。升档即时、降档计时防抖；`daily_requests` 是控制器日配额。档位的具体阈值与防抖次数以 [backseat/sensors/screen.py](../backseat/sensors/screen.py) 为准（它是唯一出处，别处不复述）。

**双频率与环形缓冲**：采集 1s（中/高档）、低档 60s；帧先进 RAM 环形缓冲（~120s），不落盘。空帧在入环前拦截（灰度近均值的 std 低于阈值视为空白），直接拉低档。

**hamming 边界统一**：≥6 放行、<6 拦截——L0 门控与挑选器用同一个边界，不各写一个。

**挑选器**：从环中挑"有意思"的帧：hamming 尖峰、大事件帧、振荡标记帧，按上一个入选基准相对 `hamming≥6` 入选，按时序最多 `batch_max`（6）张拼批 → 一次 L1 调用。冷启动（尚无基准）首个非空帧直接入选、建立基准；未入选的帧随环淘汰（宁多勿漏由高档采样保证）。多给图不增总 token 量级（同批压缩），宁多勿漏。挑选是启发式，不是理解——保守地 Always 给，模型自己消化。

**删除感知（振荡签名）**：像素级精确删除检测做不到（等 ATSPI 2.1），引擎改测**振荡签名**：hamming 序列 A 高→B 高，且 frame_i 与 frame_{i+2} 相似度回升 → 标记"疑似删除/回退"，把删前/删中/删后帧序列成批给见证层，prompt 要求显式记录"删除了什么"。20s 处理间隔 + 序列批帧，使删改不再隐身。

## 压缩与折叠

记忆金字塔的竖轴是压缩管道（[6_ARCHITECTURE.md](6_ARCHITECTURE.md)），这里是合并的落库规则：

- 每 K 条未折叠 small 合并成 1 条 medium；每 M 条 medium 合并成 1 条 big。K/M 是参数，取值由 budget/effort 映射（映射在 [6_ARCHITECTURE.md](6_ARCHITECTURE.md)），不在本层写死。
- 合并 = 子消息 `folded=1` + `parent_id` 指向吸收它的上一级消息；行不删，仍可下钻（见上节）。所以"压缩产物"不是另一种东西，是更高级的消息。
- **会话流**不是独立存储，是 `messages WHERE level='small' AND folded=0` 的查询视图；prefix 组装时渲染为文本。文字白菜价，删了没有任何好处。
- big 永不合并、重启加载。合并行的 `obs_ids` 为空，证据只能从叶子 small 汇总——这正是 `_gather_obs_ids` 存在的原因。
- `parent_id` 的下钻查询由索引 `idx_msg_parent` 支撑；该索引由 `Store.__init__` 的 `executescript` 自动补建，旧库首次以写模式打开时建（秒级），不靠单独的迁移步骤。

## 驱逐

L0 原始帧受存储预算约束；L1–L3 全文字 <1MB/天，永久保留（预算策略侧见 [6_ARCHITECTURE.md](6_ARCHITECTURE.md)）。

- 算法：L0 FIFO，按 `ts_wall` 从旧到新驱逐；超预算时每 60 tick 触发一次（[backseat/store.py](../backseat/store.py) 的 `evict_l0`）。
- 豁免（不删 `ref`，保留 `ref_full`）：`pinned=1` 的 pin 帧、**最近一条 anchor 消息引用**的 observations 帧、全分辨率副本。
- 驱逐后 flashback 降级为纯文字：观察行保留，仅 `ref` 置 NULL；原帧大小写入该行 `meta.evicted_bytes`（无损溯源）。`storage_used()` 仍**只累加 `bytes`**，驱逐后不虚高、也不二次计数。
- 同一路径被多行共享时：删除前先查该路径的所有行，只要任一行豁免（pinned / anchor 引用 / 全分辨率副本）就不删；`unlink` 成功后把所有 `ref=该路径` 的行一并 `ref=NULL`、`bytes=0`、写 `evicted_bytes`，行与 `storage_used()` 语义一致，无残留幻影字节；`freed_bytes` / `evicted` 按路径只计一次。
- 水位记于 `state.evict`（运行侧读数见 [9_IMPLEMENTATION.md](9_IMPLEMENTATION.md)）。

## 会话边界推导

**会话起点**由 observations 的实际时间线推导，不靠计数器：从最新观察向前回溯，相邻观察间隔 > `gap_close_min` 即断开；只读 observations（走 `idx_obs_ts`），**不依赖 `state` 里的持久化起点**——所以停机/重启跨过 gap 后自动自愈，不会把停机时长算作连续活跃。`gap_close_min` 默认 30 分钟（[backseat/config.py](../backseat/config.py)）。

同一边界也用在别处：> `gap_close_min` 后的首次活动是 `idle_resume` 大事件；系统睡眠/墙钟跳变是天然会话边界（暂停/合盖强制闭合当前 chunk）。

## 称呼后缀变体与已知粒度问题

词面生成取名词前连续汉字、去「的」、留 2–6 字后缀变体（长→短）。代价：一个名字生多个后缀变体，于是"红发大衣男子"与"黑发大衣男子"在共同后缀上被认作同一称呼而**整行拒收**；这也是不能做删名字文本手术的原因（最长变体会吞掉邻词与标记词）。

要不要收紧认同一称呼的口径是口味问题，等下一次实跑读数；未决问题（含行 `26028` 的两个修法）收在 [0_STAGING.md](0_STAGING.md)，本层不在这里做决定。
