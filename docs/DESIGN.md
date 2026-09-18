# Backseat 设计文档

## 1. 目标与非目标

**目标（Release 2.x）**：感知层——持续观察，构建分层长期记忆，经出口表达（弹幕）。

**非目标（V2 明确不做）**：帮助层（代替用户执行动作）。V3 目标，架构预留接口。

**北极星（方向性，不实现）**：个人画像 → 可导出、可联合 → 组织级记忆（A2A），解决集体痛点。对架构的唯一要求：画像与时间线必须是**一等公民的导出物**，数据模型保持干净。

## 2. 正交信息网格

时间轴（瞬间/短期/中期/长期）× 语义轴（原始/表象/行为/画像）：

| 语义↓ / 时间→ | 瞬间 | 短期 | 中期 | 长期 |
|---|---|---|---|---|
| **原始** | 帧+ATSPI快照+事件流（磁盘，预算内可驱逐） | — | — | — |
| **表象** | mini：diff/转写/描述（JSON） | 会话流（append-only） | — | — |
| **行为/意图** | intent 字段 | 会话流 | TIMELINE mid 条目 | BIG epoch 条目 |
| **画像/哲学** | — | — | USER_MODEL 增量 | USER_MODEL 本体 |

竖读是压缩管道，横读是留存时间。竖着的每一步都在降维（像素→文字→模式），横着的每一步都在付费（存储预算换语义密度）。

## 3. 记忆金字塔

```
L0 原始层   帧+传感器事件，磁盘，TTL/预算驱逐，永不进context
messages 单表，level 三级（每条都是 message，合并=升层级）：
small   每帧事件一次，像素→文字的唯一转换点，append-only
medium  每K条small合并：{时段, 场景, 意图弧线, 关键事件, callback标签, keys}
big     每M条medium合并：{epoch, 项目, 行为模式, 名场面}，永不合并，重启加载
正交: USER_MODEL（画像，L2/L3时增量合并）
正交: KB（key唤起记事本，borrowed from sys_gal V4）
正交: expressions 表达与行为（引擎三动词 speak/propose/act；channel 只是字段）
```

### 唤起机制（移植自 sys_gal）

KB 行带 keys（定位符=唤起词）：新 mini 命中任意 key → 该行自动注入会话流。特殊键 `!always`（周期重放）、`!at=时间`（到点提醒）。compaction 时 last_shown 清零防漏。

## 4. 链路清单（每一环的走通条件）

| 链路 | 触发 | 输出 | 失败→处理 |
|---|---|---|---|
| A 传感→事件 | pHash>ε 或大事件 | 帧ref+传感器快照 | 采集失败→跳帧计数 |
| B 事件→mini | A | JSON(type/diff/intent/candidate_project?/importance?/roast?) | 解析失败→丢弃+日志 |
| C mini→会话流 | B | append + key提取 | — |
| D 会话→mid | K条或大变化 | 摘要+keys+标签+画像增量 | 坏输出→重试1次后跳过 |
| E mid→big | M条 | epoch+画像合并 | — |
| F 任意→flashback | key命中/显式查询 | 逐字文本+帧ref | 帧被驱逐→文字降级 |
| G 事实→人格层 | importance≥线 / KB命中 / 周期digest / 大事件 | 表达草稿(content/mood/salience) | 人格调用失败→本轮沉默 |
| H 草稿→出口 | 引擎闸门（节流/去重/沉默） | expressions 行 + 出口渲染 | 去重失败→静默 |

**重启验收标准**：重启后第一轮，系统必须能答对"用户现在在干什么"。

## 5. 插件接口（核心只认这五个）

```
Sensor(t) -> Event[]          # 屏幕/ATSPI/D-Bus/git/…
Outlet(expr) -> status回执     # 弹幕/人偶/通知/日志/…
Persona -> Expression草稿      # ★人格层：加载人设+下层事实，独立LLM调用，
                               #   只负责"怎么说"，不负责"是什么"
Model(messages) -> text       # litellm/ollama/本地VLM
Store                          # SQLite 默认；未来向量库
```

**人格层公理（Round 4）**：事实归引擎，风格归人格。见证层/压缩层/侦探层只产出事实
（diff/意图/档案），永远不吐槽；人格是插件，订阅下层事实+KB命中+USER_MODEL，
独立装配自己的冻结前缀（人设prompt），产出表达草稿；引擎闸门（节流/去重/沉默）
施加于草稿之后。N个人格 = N个插件实例 = N种口吻，互不污染核心。

装配靠配置文件。跨平台 = 换 Sensor 适配器（Linux: x11grab+ATSPI；Win: UIA；mac: AX），核心零改动。

## 6. LLM 参与 Slider

```
L0 纯先验（无LLM）：pHash门控、ATSPI差分、哈希去重 —— 进场线: 永不
L1 mini压缩：     进场线 = 新信息出现 且 表示换模态（像素→文字）
L2 中/大压缩：    进场线 = K条mini / M条mid（周期性，无事件驱动）
L3 侦探agentic：  进场线 = 见证层召唤(investigate字段) 或 调度器规则
```

纪律：**确定性算法能做的，模型不碰**。侦探层工具 = bash 包 bwrap（全盘只读 + 断网），12轮上限，`--detective-bash` 可关。

## 7. Context 组装与前缀不变性

```
[system 冻结] [schema 冻结] [USER_MODEL] [BIGS…] [MIDS…] [MINIS…只增]
→ [KB命中行] → [expressions 尾部] → [本轮: 时间戳+模态载荷(≤1图 或 纯文本)]
```

- 缓存失效只发生在压缩边界（mini→mid、mid→big）
- **时钟放最末尾**——稳定前缀里不许出现任何每帧变化的东西
- 每帧最多 1 张图；历史全文字；单条压缩（图片→文字）只发生在 L1

## 7.5 记忆可见性、双频率采样与轨迹（Round 4）

**知识 vs 历史**：kb 与 USER_MODEL 是**知识**（名词——现在是什么）；messages 是
**历史**（动词——发生过什么，flashback 的领地）。两者都注入 context，但语义标签不同。

**记忆引擎必须对模型可见**：所有冻结 system prompt 含一段固定说明（缓存安全）：
"你会看到 USER_MODEL（你对用户的当前理解）、MEDIUM/BIG（历史纪要）、
[KB命中行]（相关知识自动浮现）、SMALLS（事实流水）——由记忆引擎维护注入，
不是你生成的。"防止模型把注入的记忆当幻觉或忽略其存在。

**双频率**：采集频率 ≠ 处理频率。
- 采集 1s（中/高档），低档 60s——帧先进 RAM 环形缓冲（~120s），不落盘
- 引擎层（无LLM，保守）从环中挑选"有意思"的帧：hamming 尖峰、大事件帧、
  振荡标记帧，按时序最多 6 张拼批 → 一次 L1 调用。多给图不增总 token 量级
  （同批压缩），宁多勿漏
- 挑选是启发式，不是理解——保守 Always 给，模型自己消化

**删除感知（用户删了又改）**：像素级精确删除检测做不到（等 ATSPI 2.1），但引擎
可测**振荡签名**：hamming 序列 A高→B高 且 frame_i 与 frame_{i+2} 相似度回升
→ 标记"疑似删除/回退"，把删前/删中/删后帧序列成批给见证层，prompt 要求显式
记录"删除了什么"。20s 处理间隔 + 序列批帧 = 删改不再隐身。

**Trajectory（开发期全量）**：每次 LLM 调用完整落盘 JSONL（messages、响应原文、
reasoning、usage、时延）——这是模型的思考轨迹，调试/优化的唯一依据。
**实时统计**：逐调用 tokens、缓存命中/未命中 tokens、时延；周期性 storage 字节
水位与增量。终端用户发布时据此预估成本，反调预算与策略。

**一期无输入**：V1/V2 纯输出。用户输入通道（STDIN/屏幕输入框）属二期；
CLI ask 亦为 2.2 预览。

## 8. 存储预算

帧存双分辨率：常规 1600px JPEG q6（≈150KB）；pin 帧/大事件帧补存全分辨率（供回头看精读）。活跃约 300帧/时 ≈ 1GB/天。预算默认 20GB ≈ 3周原始数据。L1-L3 全文字 <1MB/天，永久保留。驱逐：L0 FIFO + 例外（ANCHOR引用帧、pin帧、全分辨率副本不驱逐）。驱逐后 flashback 降级为纯文字。

## 10. Grill Round 2 — 决策账本（续）

- **锁屏不是事件**：锁屏=不动的画面，pHash门控天然处理。LOCK/UNLOCK 从大事件枚举移除
- **消息本体论**：压缩产物不是另一种东西，是更高级的消息。三表合一 -> `messages` 单表，`level ∈ {small, medium, big}`；合并 = 子消息 folded=1 + parent_id（行不删可下钻）；会话流 = `level='small' AND folded=0`。汉诺塔式：n条低级合并成1条高级
- **认知边界**：深潜前 mini 禁止业务定性（只说证据支持的话）；深潜(L3)触发封闭枚举：①首次目击未知项目 ②潜后累积重大变化 ③长gap恢复 ④非用户手笔的大变更（AI coding爆发）。潜间只跟踪状态
- **DBus 降级**：仅 opportunistic 记录桌面通知，不承诺覆盖；成败检测主路径 = 屏幕文字理解（ATSPI > VLM截图）；bash hook 不做
- **出口标准**：mood 闭集（neutral/focused/happy/frustrated/mocking/guilty/anxious/excited/shocked/tired）+ salience 连续值；出口插件声明支持集，未知降级 neutral；介质翻译（颜色/字号/打断等级）是插件对 mood/salience 的映射。固化于 docs/SCHEMA.md（M2交付）
- **采样节奏控制器**：采样率 = 场景动力学的函数（pHash差分EMA）。低->60s退避 / 中->20s默认 / 高->2s+批帧（4~6帧网格一次调用）/ 大事件立即。升档即时、降档计时防抖。daily_requests 是控制器日配额
- **关键瞬间 pin**：场景切换/全绿通过/报错风暴的帧自动 pin（驱逐豁免）——画像原料来自细节

## 10.G4 — Grill Round 4 — 决策账本

- **人格层独立成插件**：事实归引擎（见证/压缩/侦探只产事实），风格归人格（订阅事实+KB+USER_MODEL，独立冻结前缀，独立LLM调用，产表达草稿）。N人格=N实例。引擎闸门施加于草稿后
- **侦探确认只读**：产出文本档案交引擎写库（引擎是一切写入者）；断网维持（暂无需要网络的用例，待定）
- **记忆可见性**：冻结prompt含固定记忆引擎说明段（USER_MODEL/MEDIUM/KB/SMALLS各是什么），防模型遗忘或当幻觉
- **双频率采样**：采集1s进RAM环（不落盘），引擎启发式挑选≤6帧拼批，宁多勿漏；删除/回退用振荡签名检测+序列批帧，prompt显式报删除
- **Trajectory 全量落盘**（开发期）+ 逐调用 token/缓存命中/时延统计 + 存储水位增量——面向终端用户时可预估可反调
- **一期无输入**：纯输出；STDIN/输入框/CLI ask 属二期

## 10.G2 — Grill Round 3 — 决策账本（审查驱动的精化）

- **effort 门控语义**：门控对象 = 侦探的 investigate 召唤（effort<0.7 关闭）；调度器枚举触发（首见/重大变化/gap/AI爆发）不受门控；侦探轮次上限随 effort 缩放（0.5→8轮）
- **expressions.status 枚举**：sent(已投递)→shown(出口回执确认渲染)；propose/act 走 accepted/rejected/done。回执由出口插件推进
- **两个重要度，两个名字**：mini.importance = 事件重要度（驱动 pin/深潜）；expression.salience = 发言重要度（驱动字号/打断等级）。同名两义已拆分
- **mini schema 增 candidate_project 字段**：深潜触发判定"首次目击"的输入（否则调度器无从得知待检词）
- **hamming 边界统一**：≥6 放行、<6 拦截
- **侦探产物不进 messages**：只落 kb/state；"报告已归档"走不计 K 的旁路行
- **obs 增 display 列**（多显示器预留，V2 只抓主屏）

## 10.G1 — Grill Round 1 — 决策账本

已关闭的问题：

- **表结构公理**：表描述引擎本体——观察 -> 理解 -> 表达/执行。插件与输入输出手段不是表，是字段值（observations.sensor / expressions.channel）。obs 单表承载一切传感器（screen/atspi/dbus/…），expressions 单表承载一切出口（danmaku/notification/avatar/…）。见证层只输出语义（content/mood/salience），介质呈现（颜色/字号）是出口插件对 mood/salience 的映射——弹幕不是词汇，是翻译结果

- **low级消息压缩后去留**：不删除，`folded=true` + `parent_id` 指向吸收它的上一级消息；flashback 默认查 medium/big，可显式下钻 small。文字白菜价，删了没有任何好处
- **会话流是什么**：不是独立存储，是 `messages WHERE level='small' AND folded=0` 的查询视图；prefix 组装时渲染为文本
- **USER_MODEL/ANCHOR/计数器住哪**：`state` 表（kv）。独立的金字塔表装不下它们——grill 揪出的真矛盾
- **"大事件"定义**：封闭枚举：窗口切换、workspace切换、全屏切换、恢复空闲（>30min后首次活动）、display拓扑变化。（Round 2：锁屏移除——静止画面门控自处理）新增须改此清单
- **pHash 阈值 ε**：初始固定（Hamming ≥6/64），可配；M1 确认门就是调它的
- **事件去重**：总线按 (kind, key, 时间窗) 去重，防多传感器双发
- **时间戳**：墙钟（用户时间）+ 单调钟（区间计算）双轨；所有表两列都有
- **暂停/合盖**：gap > 30min 强制闭合当前 chunk——系统睡眠是天然会话边界
- **roast 的 color/size**：属于 Outlet 扩展字段，不进核心 mini schema；Persona 负责产出
- **多显示器**：schema 现在就带 `display` 列；V2 只抓主屏
- **崩溃恢复**：SQLite WAL + 幂等写入；引擎无状态，状态全在 DB
- **Persona 语言**：V2 中文优先；英文 Persona 是独立插件文件，发布前补

- **隐私 Q1（已拍板）**：原始帧永不做遮蔽/打码——不为此复杂化架构。隐私只发生在文字化边界：L1 提示词含脱敏规则（"转写/描述文字中若出现密码等敏感内容，以▇代替"）。提示词方案，非系统机制
- **成本 Q2（已拍板）**：V2 开发期不设上限，逐帧计量仅做统计；效果验证后再定预算算法

## 11. 预算驱动的弹性策略（无级调节）

所有运行参数都不是常量，而是用户预算的函数。用户设预算，引擎反推参数，实时重算不重启：

| 用户预算（输入） | 引擎推导（输出） |
|---|---|
| `storage_bytes`（5G～200G） | L0 留存天数、全分辨率副本配额、驱逐激进度 |
| `daily_requests`（可选，不设=不限） | L1 cadence、pHash ε（收紧门控）、coalescing 窗口 |
| `effort` | anchor 频率、K/M 压缩粒度、侦探层开关 |

映射器先实现为阶梯函数（有极），接口按连续值设计（无极）。这就是"所有策略都有柔性"的落地形式：没有魔法数，只有预算的导出值。

## 9. 决策记录

- **不基于 pi 宿主**：pi 扩展是会话作用域（官方文档禁止扩展工厂启动定时器/文件监视器），与 7×24 守护进程形态相反；context 组装是核心 IP，不能让渡给宿主；AGPL 核心不应强绑 node。pi 降级为未来可选的"手臂适配器"（V3 帮助层的一种执行后端）
- **自研最小工具循环**：OpenAI 兼容 tool_calls + while 循环 + 工具注册表（read_file/list_dir/atspi_query/run_readonly），约150行，无需任何 agent 框架依赖。subagent = 同一 Store 上的独立 Persona 循环实例
- **不要 OCR**：独立 OCR 丢空间与语义 grounding。文字获取 = ATSPI（精确）> VLM转写模式（语义）；"派画在画上还是logo"只有语义模型分得清
- **空帧/不变帧不调 LLM**：pHash 门控 + blank 检测在 L0 拦截；时间戳自证空闲，无需计数器
