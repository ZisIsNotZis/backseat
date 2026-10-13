# Ticket: wp-insight-session —— 修复"连续活跃 124.5 小时"误报

- 时间：2026-10-13T18:27:43+08:00
- 角色：worker（WP 执行）
- 分支：`pi-subagents/worker-WP-wp-insight-session-...-s0-t0`
- 基线：`f67ab74 README 链接运行手册`
- 范围：`backseat/insight.py`、`tests/test_insight.py`（不改人格层/闸门/词表/kind 集合/docs）

## 1. 症状与证据

守护进程 10-08 → 10-13 停机约 125h 后恢复。`~/backseat-data/trajectory/2026-10-13.jsonl`
中 persona 收到的冻结上下文含：

```
[洞察候选] 这一轮已经连续活跃 124.5 小时
```

本轮模型输出 `{"silence": true}`（未被误导开口），但候选本身错误，并持续污染冻结
context（`_digest_context` 每轮注入）。

## 2. 根因

`InsightMiner.candidates()` 旧逻辑：

```python
sess_start = self.store.get_state("session_start_wall")          # 跨进程持久化的可能陈旧值
last_obs = ...SELECT max(ts_wall) FROM observations
if last_obs and (not sess_start or now - last_obs > gap_close_min*60):
    self.store.set_state("session_start_wall", last_obs)         # 只在"最近观察距今很久"时重置
    sess_start = last_obs
if sess_start and now - sess_start >= 6*3600:
    ... long_session ...
```

重置条件 `now - last_obs > gap_close_min*60` 只在"距最近观察已超过 gap"时成立。停机 → 恢复后
新一轮观察持续到来，该条件永不触发，`session_start_wall` 仍是停机前写下的值；于是
`now - sess_start` 把整段停机也算成"连续活跃"，得出 124.5h。

## 3. 新算法（验收标准 1/2）

不再依赖持久化 `state`。每次调用从 `observations` 真实时间线推导当前连续会话起点：

`backseat/insight.py::InsightMiner._session_start(now)`

1. `last = SELECT max(ts_wall) FROM observations`（命中 `idx_obs_ts`，O(log n)）。
2. 若 `now - last > gap_close_min*60`：当前无活跃会话，返回 `None`
   （DESIGN：gap>30min 闭合会话；同时避免"睡前 23:00 → 早 07:00 无新观察"被算成连续活跃的
   同类误报）。
3. 从 `last` 向前分批回溯（`WHERE ts_wall < ? ORDER BY ts_wall DESC LIMIT 512`，走
   `idx_obs_ts` 倒序区间扫描）；相邻间隔 `> gap_close_min*60` 即断为会话边界；返回该段最早观察。
4. `candidates()` 用 `now - session_start >= 6*3600` 判定 `long_session`（阈值不变）。

自愈性：起点完全由观察表算出，停机/重启跨过 gap 后自然得到恢复后的起点，无需手写重置。

效率护栏：`_SESSION_BATCH=512`、`_SESSION_SCAN_MAX=20000`。上界保证"整库一段连续"也不会
无界回溯；最细采样 2s/帧下 20000 行仍横跨 ≥11h，远超 6h 门槛，不会因截断漏报 `long_session`。

## 4. 验收标准对照

| # | 要求 | 实现 | 验证 |
|---|---|---|---|
| 1 | 起点由 observations 时间线推导、不依赖陈旧 state、可自愈 | `_session_start` 只读 obs，删除对 `session_start_wall` 的读写 | 用例1：预置陈旧 `state.session_start_wall=t0` 仍得 1h |
| 2 | 不全表扫描、带索引、毫秒级 | `max(ts_wall)` + `ts_wall<? ORDER BY ts_wall DESC LIMIT` 均命中 `idx_obs_ts` | EXPLAIN QUERY PLAN + 基准（真实形态 0.7ms） |
| 3 | `long_session` ≥6h 阈值不变；其余 kind 不受影响；`insight_anomaly=false` 仍整体跳过 | 仅替换起点来源，阈值/早返回不动 | 用例2 7h 触发；`test_insight` 既有 repeat/revival/streak/hour 断言全过 |
| 4 | 新增覆盖：跨 100h gap=1h；连续 7h 触发；gap==阈值边界 | `tests/test_insight.py::session_insight()` | 见下 |
| 5 | 既有测试全过 | — | `test_m0..m5`、`test_insight` 全 PASS |

## 5. 命令与结果

```
$ python3 tests/test_insight.py
...
session OK: 跨 100h 停机 gap 后连续会话=1h（陈旧 state 被忽略）
session OK: 连续 7h → long_session 触发: 这一轮已经连续活跃 7.0 小时
session OK: 边界 gap==阈值不断，>阈值即断
Insight smoke: ALL PASS
```

```
$ for t in test_m0 test_m1 test_m2 test_m3 test_m4 test_m5 test_insight; do python3 tests/$t.py; done
M0/M1/M2/M3/M4/M5 smoke: ALL PASS；Insight smoke: ALL PASS
```

EXPLAIN QUERY PLAN（两条查询均走索引）：

```
SELECT max(ts_wall) m FROM observations                     -> SEARCH ... USING COVERING INDEX idx_obs_ts
SELECT ts_wall FROM observations WHERE ts_wall < ? ORDER BY ts_wall DESC LIMIT 512
                                                            -> SEARCH ... USING COVERING INDEX idx_obs_ts (ts_wall<?)
```

基准（临时库）：

- 缺陷真实形态（7802 行，跨 100h gap）：`_session_start` **0.69 ms**，span=1.00h，无 `long_session`。
- 最坏形态（36000 行整库连续无 gap）：**22 ms**（命中 20000 行护栏上界），span 仍 ≥6h。

## 6. 改动文件

- `backseat/insight.py`：删除 `session_start_wall` 读写，新增 `_session_start()` 与回溯常量。
- `tests/test_insight.py`：新增 `session_insight()`（三用例）并从 `main()` 调用。

## 7. 遗留 / 集成者待办

- 陈旧键 `state.session_start_wall` 会残留在既有 DB 中；新代码不再读写，无害，不做迁移。
- 文档同步（本 WP 排除，交由集成者）：DESIGN §4 链路 G / §10 会话边界的洞察异常描述，
  及任何提及 `session_start_wall` 的说明，需改为"会话起点由 observations 时间线推导"。
