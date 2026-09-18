# Backseat Schema v1（M2 固化）

核心语义层 schema。改动即迁移（ROADMAP M0 门已定稿表结构；本文固化语义契约）。

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

`kind ∈ {speak, propose, act}`；`channel` = 出口插件 id（danmaku|notification|cli|…）；
`mood` 闭集：`neutral focused happy frustrated mocking guilty anxious excited shocked tired`；
`salience` 0~1 连续值；`status ∈ {sent, shown, accepted, rejected, done}`
（sent→shown 由出口回执推进；propose/act 走 accepted/rejected/done）。
出口插件声明支持集，未知 mood 降级 neutral；介质翻译（颜色/字号/打断等级）是插件对
mood/salience 的映射——弹幕不是词汇，是翻译结果。

## 大事件（封闭枚举，新增须改 backseat/bus.py EventKind）

`window_switch / workspace_switch / fullscreen_toggle / idle_resume / display_change`
（锁屏不是事件——静止画面由 pHash 门控自处理；睡眠唤醒=墙钟跳变，天然会话边界）

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

时钟放最末；缓存失效只发生在合并边界。逐调用计量落 `metrics` 表；全量对话落
`~/backseat-data/trajectory/YYYY-MM-DD.jsonl`。

## KB（唤起记忆，kb 行）

`keys`（定位符 json 数组）+ `desc` + `kind ∈ {fact, reminder, contact, proposal}` +
`special ∈ {!always, !at=…}` + `open` + `last_shown`。新 mini 文本命中任意 key → 该行注入
[KB命中行]；`!always` 周期重放；`!at=时间` 到点提醒。
