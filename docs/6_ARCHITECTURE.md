# 6_ARCHITECTURE — 架构层

这一层是子系统的边界与质量属性策略：信息怎么分格、记忆怎么分层、链路上每一环怎么失败、LLM 在哪儿进场、context 为什么这样拼、采样与存储为什么这样取舍、以及几个不回头的架构决定。
它以"屏幕只给题面、物质来自屏幕之外、出口不承诺动作"为既定前提（前提本身见 [2_GOAL.md](2_GOAL.md)）。
字段与字段级契约不在这里（见 [7_INTERFACE.md](7_INTERFACE.md)）；模块内部算法、写入规则、具体阈值不在这里（见 [8_DETAIL.md](8_DETAIL.md)）；哪些预算换哪些能力不在这里（见 [3_REQUIREMENT.md](3_REQUIREMENT.md)）。

# EXPLICIT

本区为空：架构层没有用户原话直接落在这里。用户拍板的目标与表达原则分别住在 [2_GOAL.md](2_GOAL.md)、[5_PRODUCT.md](5_PRODUCT.md)，用户拍板的隐私与成本两条约束住在 [3_REQUIREMENT.md](3_REQUIREMENT.md)。下面全部是由此推导出的设计决定。

# CONFIRMED

本区为空：本节没有用户"已确认"而未改述的事项（Grill 轮是审查驱动的精化，不是用户确认）。

# INFERRED

## 正交信息网格：时间 × 语义

一帧像素在时间轴与语义轴上各走一格；横竖两个方向回答两个不同问题。

| 语义↓ / 时间→ | 瞬间 | 短期 | 中期 | 长期 |
|---|---|---|---|---|
| **原始** | 帧+ATSPI快照+事件流（磁盘，预算内可驱逐） | — | — | — |
| **表象** | mini：diff/转写/描述（JSON） | 会话流（append-only） | — | — |
| **行为/意图** | intent 字段 | 会话流 | TIMELINE mid 条目 | BIG epoch 条目 |
| **画像/哲学** | — | — | USER_MODEL 增量 | USER_MODEL 本体 |

竖读是压缩管道，横读是留存时间。竖直每一步都在降维（像素→文字→模式），横向每一步都在付费（存储预算换语义密度）。

## 记忆金字塔

- **L0 原始层**：帧+传感器事件，落磁盘，按 TTL/预算驱逐，**永不进 context**。
- **messages 单表，level 三级**：每条都是 message，合并＝升层级。
  - `small`：每帧事件一次，像素→文字的唯一转换点，append-only。
  - `medium`：每 K 条 small 合并一次。
  - `big`：每 M 条 medium 合并一次；永不合并，重启加载。
  - 每一级合并出哪些内容（字段集）见 [7_INTERFACE.md](7_INTERFACE.md)，合并的写入规则见 [8_DETAIL.md](8_DETAIL.md)。
  - 合并是汉诺塔：n 条低级合成 1 条高级，子消息 `folded=1` + `parent_id`（行不删可下钻）。
- **正交三件**（不在金字塔层级里，与它并列）：
  - `USER_MODEL`：画像，L2/L3 时增量合并。
  - `KB`：key 唤起的记事本（borrowed from sys_gal V4）。
  - `expressions`：表达与行为，引擎三动词 speak/propose/act；channel 只是字段。

三级消息与三件正交物的**字段集、建表与合并的写入规则**见 [7_INTERFACE.md](7_INTERFACE.md) 与 [8_DETAIL.md](8_DETAIL.md)；本层只认"它们是不同的层与旁路，不是同一种东西"。

**唤起机制（移植自 sys_gal）**：KB 行带 keys（定位符＝唤起词），新 mini 命中任意 key → 该行自动注入会话流；特殊键 `!always`（周期重放）、`!at=时间`（到点提醒）；compaction 时 last_shown 清零防漏。key 语义与命中规则见 [8_DETAIL.md](8_DETAIL.md)。

## 八条链路与失败处理

每条链路都定义"走通的条件"和"断了怎么办"；失败不抛给用户，只在链路内部降级或沉默。

| 链路 | 触发 | 输出 | 失败→处理 |
|---|---|---|---|
| A 传感→事件 | pHash>ε 或大事件 | 帧ref+传感器快照 | 采集失败→跳帧计数 |
| B 事件→mini | A | mini 结构（字段见 [7_INTERFACE.md](7_INTERFACE.md)） | 解析失败→丢弃+日志 |
| C mini→会话流 | B | append + key提取 | — |
| D 会话→mid | K条或大变化 | 摘要+keys+标签+画像增量 | 坏输出→重试1次后跳过 |
| E mid→big | M条 | epoch+画像合并 | — |
| F 任意→flashback | key命中/显式查询 | 逐字文本+帧ref | 帧被驱逐→文字降级 |
| G 事实→人格层 | importance≥线 / KB命中 / 周期digest / 大事件 | 表达草稿（字段见 [7_INTERFACE.md](7_INTERFACE.md)） | 人格调用失败→本轮沉默 |
| H 草稿→出口 | 引擎闸门（节流/去重/沉默） | expressions 行 + 出口渲染 | 去重失败→静默 |

**会话边界是推导量，不是持久化状态**：洞察的「会话起点」从最新观察向前回溯，相邻观察间隔超过阈值即断开；只读 observations，不依赖 state 里的持久化起点，因此停机/重启跨过 gap 后自动自愈，不会把停机时长算作连续活跃。阈值与推导算法见 [8_DETAIL.md](8_DETAIL.md)。重启后第一轮"答对用户在干什么"是确认门，判定口径见 [0_ROADMAP.md](0_ROADMAP.md)。

## 感知面与降级

- **锁屏不是事件**：锁屏＝不动的画面，pHash 门控天然处理，LOCK/UNLOCK 从大事件枚举移除。大事件的封闭枚举清单见 [7_INTERFACE.md](7_INTERFACE.md)。
- **D-Bus 是机会主义的**：仅记录桌面通知，不承诺覆盖。成败检测的主路径是屏幕文字理解（ATSPI 优先于 VLM 截图）；bash hook 不做。
- **传感器换平台＝换适配器**：核心零改动；插件接口见 [7_INTERFACE.md](7_INTERFACE.md)。

## LLM 参与 slider

LLM 不是每步都进；进场线按层写死。

```
L0 纯先验（无LLM）：pHash门控、ATSPI差分、哈希去重 —— 进场线: 永不
L1 mini压缩：     进场线 = 新信息出现 且 表示换模态（像素→文字）
L2 中/大压缩：    进场线 = K条mini / M条mid（周期性，无事件驱动）
L3 侦探agentic：  进场线 = 见证层召唤(investigate字段) 或 调度器规则
```

纪律：**确定性算法能做的，模型不碰**。

**认知边界**：深潜前 mini 禁止业务定性，只说证据支持的话；深潜（L3）触发封闭枚举——①首次目击未知项目 ②潜后累积重大变化 ③长gap恢复 ④非用户手笔的大变更（AI coding 爆发）。潜间只跟踪状态。

**effort 门控**：门控对象是侦探的 investigate 召唤，调度器枚举触发（首见/重大变化/gap/AI爆发）不受门控；侦探轮次上限随 effort 缩放。具体门限值与轮次缩放见 [8_DETAIL.md](8_DETAIL.md)。

**侦探只读**：侦探产出文本档案交引擎写库——引擎是一切写入者；工具为 bash 包 bwrap（全盘只读 + 断网），有轮次上限。断网维持，暂无可确认的网络用例。工具契约见 [7_INTERFACE.md](7_INTERFACE.md)。

## context 组装：为什么是这个顺序

组装结果是一段前缀 + 本轮载荷；**顺序字符串本身是契约，见 [7_INTERFACE.md](7_INTERFACE.md)**。这里只记理由：

- **缓存失效只发生在压缩边界**（mini→mid、mid→big）——稳定前缀越长，命中率越高，所以一切冻结内容都在前、一切易变内容都在后。
- **时钟放最末尾**——稳定前缀里不许出现任何每帧变化的东西。
- **每帧最多 1 张图；历史全文字；单条压缩（图片→文字）只发生在 L1**——图片是当轮载荷，不是历史的一部分。

**记忆引擎必须对模型可见**：所有冻结 system prompt 含一段固定说明（缓存安全），讲清 USER_MODEL / MEDIUM / BIG / KB命中行 / SMALLS 各是什么、由记忆引擎维护注入而非模型自己生成，防止模型把注入的记忆当幻觉或忽略。

**知识 vs 历史**：kb 与 USER_MODEL 是**知识**（名词——现在是什么）；messages 是**历史**（动词——发生过什么，flashback 的领地）。两者都注入 context，但语义标签不同。

## 人格层：事实归引擎，风格归人格

见证层/压缩层/侦探层只产出事实（diff/意图/档案），永远不吐槽；人格是插件，订阅下层事实+KB命中+USER_MODEL，独立装配自己的冻结前缀（人设 prompt），独立 LLM 调用，产出表达草稿；引擎闸门（节流/去重/沉默）施加于草稿之后。N 个人格＝N 个插件实例＝N 种口吻，互不污染核心。插件接口与人格契约见 [7_INTERFACE.md](7_INTERFACE.md)。

## 双频率采样与删除感知

**采集频率 ≠ 处理频率**。采集把帧先放进 RAM 环形缓冲、不落盘；引擎层（无 LLM，保守）从环里按启发式挑"有意思"的帧——hamming 尖峰、大事件帧、振荡标记帧——按时序拼批，一次 L1 调用。多给图不增总 token 量级（同批压缩），宁多勿漏；挑选是启发式，不是理解，保守 Always 给，模型自己消化。

**采样节奏控制器**：采样率是场景动力学的函数（pHash 差分 EMA），升档即时、降档计时防抖；`daily_requests` 是控制器的日配额。

**删除感知（用户删了又改）**：像素级精确删除检测做不到（等 ATSPI 2.1），改用**振荡签名**发现"疑似删除/回退"，把删前/删中/删后帧序列成批给见证层，prompt 要求显式记录"删除了什么"。

档位、环时长、批次上限、hamming 边界、振荡签名的判据与实现只住 [8_DETAIL.md](8_DETAIL.md)；本节只说为什么这样取舍。

## 存储预算

- 帧存**双分辨率**：常规 1600px JPEG q6（≈150KB）；pin 帧/大事件帧补存全分辨率（供回头看精读）。
- 活跃约 300帧/时 ≈ 1GB/天；预算默认 20GB ≈ 3周原始数据；L1–L3 全文字 <1MB/天，永久保留。
- **驱逐**：L0 FIFO + 例外（ANCHOR 引用帧、pin 帧、全分辨率副本不驱逐）。驱逐后 flashback 降级为纯文字：观察行保留，只把帧引用置空，原帧大小无损留痕。字段与驱逐算法见 [7_INTERFACE.md](7_INTERFACE.md)、[8_DETAIL.md](8_DETAIL.md)。
- **关键瞬间 pin**：场景切换/全绿通过/报错风暴的帧自动 pin（驱逐豁免）——画像原料来自细节。

**观测与反调**：开发期每次 LLM 调用完整落盘 JSONL（messages、响应原文、reasoning、usage、时延）——模型的思考轨迹是调试/优化的唯一依据。实时统计逐调用 tokens、缓存命中/未命中、时延，以及周期性 storage 字节水位与增量；终端用户发布时据此预估成本、反调预算与策略。计量表结构见 [7_INTERFACE.md](7_INTERFACE.md)。

**一期无输入**：V1/V2 纯输出；用户输入通道（STDIN/屏幕输入框）与 CLI ask 属二期。范围见 [2_GOAL.md](2_GOAL.md)。

## 决策记录

- **不基于 pi 宿主**：pi 扩展是会话作用域（官方文档禁止扩展工厂启动定时器/文件监视器），与 7×24 守护进程形态相反；context 组装是核心 IP，不能让渡给宿主；AGPL 核心不应强绑 node。pi 降级为未来可选的"手臂适配器"（V3 帮助层的一种执行后端）。
- **自研最小工具循环**：OpenAI 兼容 tool_calls + while 循环 + 工具注册表（read_file/list_dir/atspi_query/run_readonly），约150行，无需任何 agent 框架依赖。subagent＝同一 Store 上的独立 Persona 循环实例。
- **不要 OCR**：独立 OCR 丢空间与语义 grounding。文字获取＝ATSPI（精确）> VLM 转写模式（语义）；"派画在画上还是 logo"只有语义模型分得清。
- **空帧/不变帧不调 LLM**：pHash 门控 + blank 检测在 L0 拦截；时间戳自证空闲，无需计数器。
- **崩溃恢复**：SQLite WAL + 幂等写入；引擎无状态，状态全在 DB——所以重启只是重开进程，不靠内存重放。

## 预算到参数的映射

所有运行参数都不是常量，而是用户预算的函数：用户设预算，引擎反推参数，实时重算不重启。

| 用户预算（输入） | 引擎推导（输出） |
|---|---|
| `storage_bytes`（5G～200G） | L0 留存天数、全分辨率副本配额、驱逐激进度 |
| `daily_requests`（可选，不设=不限） | L1 cadence、pHash ε（收紧门控）、coalescing 窗口 |
| `effort` | anchor 频率、K/M 压缩粒度、侦探层开关 |

映射器先实现为阶梯函数（有极），接口按连续值设计（无极）。这就是"所有策略都有柔性"的落地形式：没有魔法数，只有预算的导出值。哪条预算换哪项能力的约束侧见 [3_REQUIREMENT.md](3_REQUIREMENT.md)。

**代码位置**：
- 映射器（预算→派生参数）：[backseat/config.py](../backseat/config.py) 的 `Config.derived()`——阶梯映射，返回值含 `retention_days` / `l1_cadence_s` / `anchor_every_n` / `detective_enabled`。
- 预算输入面：[config.example.toml](../config.example.toml) 的 `storage_bytes` / `daily_requests` / `effort`。
- 驱逐执行：[backseat/__main__.py](../backseat/__main__.py) 的周期检查（`storage_used() > storage_bytes` → `evict_l0()`）。
- 水位与驱逐：[backseat/store.py](../backseat/store.py) 的 `storage_used()` / `evict_l0()`。
