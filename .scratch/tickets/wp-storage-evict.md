# WP: wp-storage-evict —— L0 原始层存储驱逐（DESIGN §8）

- 建档：2026-10-13T18:27:13+08:00
- 基线：f67ab74（worktree 分支 pi-subagents/worker-WP-wp-storage-evict-…-s0-t0）
- 角色：worker（本 worktree 唯一写入者）
- 状态：实现完成，测试通过，待集成者同步 docs

## 目标

填补 RUNBOOK「待校准/待办」中的"存储驱逐未实现"。按 DESIGN §8 实现 L0 原始层
FIFO 预算驱逐，使 `storage_used()` 收敛到 `cfg.storage_bytes`，并接入守护进程主循环。

## 范围 / 排除

- 做：`Store.evict_l0()`；主循环周期接入 + `state.evict` 水位记账；`tests/test_evict.py`。
- 不做（任务排除）：不改帧采集/入选逻辑；不改 pin 策略；不改 `docs/`（文档同步由集成者做）。

## 验收标准与实现

1. `Store.evict_l0(budget_bytes, chunk=200)`：按 `ts_wall` FIFO 最旧优先，删 `ref`
   指向的 JPEG 并把该行 `ref` 置 NULL（**保留行**：时间线与 flashback 降级为纯文字，
   DESIGN §8），直到 `storage_used() <= budget_bytes` 或无可驱逐对象。返回
   `{freed_bytes, evicted, exempt, missing_files, used_before, used_after}`。
2. 豁免：`pinned=1`；最近一条 `messages`(level='small', type='anchor') 的 `obs_ids`
   引用帧；`ref_full IS NOT NULL` 的全分辨率副本（保留 `ref` 与 `ref_full` 两者）。
3. 幂等/健壮：文件已缺失不报错（计入 `missing_files`）；`ref IS NULL` 行不在候选内；
   预算已满足时零动作；全豁免/候选耗尽时收敛退出。
4. 接入 `backseat/__main__.py`：每 60 tick，`storage_used() > cfg.storage_bytes` 时
   调用驱逐，打印水位，`set_state("evict", {ts_wall, ...stats})` 记账。仅超预算才动作，
   不增加每 tick 开销。
5. `tests/test_evict.py`：临时目录 + 真 1x1 JPEG 占位文件，覆盖上述语义。
6. 全部既有测试仍通过。

## 驱逐语义与该设计取舍的理由

- **行保留、只置 `ref=NULL`**：DESIGN §8 明确"驱逐后 flashback 降级为纯文字"。
  文字白菜价（§10.G1），删行会破坏 messages→obs 溯源链下钻；`kb.flashback` 已按
  `ref` 空判 `degraded`，无需改 KB。
- **`bytes` 归零**：`storage_used()` = `SUM(observations.bytes)`，驱逐必须同步归零
  该列才能让水位真实下降；被驱逐行此时已无 `ref`/`ref_full` 文件，归零语义自洽。
  `freed_bytes` 取行内 `bytes` 记账值（与水位守恒：`used_after = used_before - freed`）。
- **FIFO 用 (ts_wall, id) keyset 分页**：不把全部候选一次性载入；游标只前进，
  豁免行只统计一次、不会导致重复扫描或死循环。
- **豁免判定**：`ref_full` 存在即整行豁免（保留 `ref`+`ref_full`），对应 §8
  "全分辨率副本不驱逐"——全分辨率副本是为日后精读而补存，若只删 `ref` 仍占预算且
  失去"回头看精读"用途，故按例外整体保留。
- **缺失文件仍完成驱逐**：`unlink` 抛 `OSError`（含 `FileNotFoundError`）时计入
  `missing_files` 并照常置 NULL/归零，避免单文件异常中断整轮驱逐。代价：极端
  "存在但不可删"（权限）时会计账超前于真实磁盘；实际帧目录由守护进程自建，风险低。
- **`exempt` 语义**：为满足预算而在扫描途中跳过（未删）的豁免行数；达标即停，
  故达标点之后的豁免行不计数。

## 命令与结果

```
python3 tests/test_evict.py                 → M-evict smoke: ALL PASS
python3 tests/test_m0.py … test_m5.py       → 均 ALL PASS
python3 tests/test_insight.py               → Insight smoke: ALL PASS
python3 -m py_compile backseat/__main__.py backseat/store.py tests/test_evict.py → OK
git diff --check                            → 无空白问题
```

场景断言（test_evict.py）：最旧非豁免被驱逐；pin/ref_full/anchor 引用帧豁免且文件
保留；被驱逐行仍在、`ref=NULL`、文件被删；`used_after <= budget`；同预算重入无动作；
缺失文件不报错计入统计；`ref=NULL` 跳过；全豁免收敛。

## 证据（改动文件）

- `backseat/store.py`：新增 `_anchor_obs_ids()`、`evict_l0()`。
- `backseat/__main__.py`：周期驱逐接入 + `state.evict` 记账 + 日志。
- `tests/test_evict.py`：新增。

## 遗留 / 交给集成者

- **docs 同步（集成者做）**：
  - `docs/RUNBOOK.md`「待校准/待办」删除或改写"存储驱逐未实现（当前 2.0GB/20GB 预算）"
    条目；建议在"验证"/运行说明补一行驱逐水位来源 `state.evict`。
  - `docs/DESIGN.md` §8 无需更正：现措辞"L0 FIFO + 例外（ANCHOR引用帧、pin帧、
    全分辨率副本不驱逐）"与实现一致。可选微调：把"驱逐后 flashback 降级为纯文字"
    补注"行保留，仅置 ref=NULL"。
- **未覆盖**：真实守护进程长跑下的驱逐触发（本工作不触碰在跑守护进程与
  `~/backseat-data`）；驱逐上限未分批限速——首次大幅超预算时会一次性删到达标。
