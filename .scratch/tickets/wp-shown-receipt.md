# Ticket: wp-shown-receipt —— 弹幕出口 status 反映引擎真实渲染结果

- 时间：2026-10-13T18:27:05+08:00（`date -Iseconds`）
- 角色：worker（实现）
- 分支：`pi-subagents/worker-WP-wp-shown-receipt-...-s0-t0`
- 目标：让 `expressions.status` 的 `shown` 语义可信——只有引擎确认落位才 `shown`，
  无空余 lane 被 `skipped`/无回执/超时都不得谎报 `shown`。

## 范围

- 改：`backseat/outlets/danmaku_engine.py`、`backseat/persona.py`、`tests/test_m5.py`
- 不动：人格 prompt、闸门、mood 映射、布局常量、lane 分配算法、`docs/`（文档由集成者同步）
- 不读不写 `~/backseat-data`，不干扰运行中的守护进程（引擎验证用 offscreen，未用 `$DISPLAY`）

## 验收标准（对应派单）

1. 引擎逐条回执 JSON 行到 stdout（`{"id":7,"status":"shown"}`/`skipped`），由 `_do_push`
   落位判定驱动，`flush=True`。
2. 输入带客户端自增 `id`；无 `id` 不回执（兼容）。
3. `DanmakuOutlet` 以 `stdout=PIPE` 启动引擎，后台线程按 id 关联回执；`render()` 在
   上界（`RECEIPT_TIMEOUT=0.5s`，≤1.0s）内等待：placed→"shown"；skipped/超时/旧引擎
   无回执→"sent"；写失败→"failed"。不无界阻塞主循环。
4. `persona.py` 两处写 `expressions.status` 逻辑不变，但状态真实。
5. `stats` 保留旧键 `shown`/`push_fail`，新增 `skipped`/`unconfirmed`。
6. 离线测试：回执关联 + 超时→sent + 旧引擎无回执→sent。
7. 视觉/点击穿透/动画不回归（未动布局常量与 lane 分配）。

## 实现要点

- 引擎：`pushRequested` 信号增 `object` 参数 id；`push(..., mid=None)`；`_do_push` 末尾
  `_receipt(mid, placed)` 打印回执（`mid is None` 静默）。duration 时限外也回 skipped，
  防客户端无界等待。
- 出口：新增 `ReceiptReader`（后台线程读引擎 stdout 行，按 id 存 `{id: status}`，
  `wait(id, timeout)` 条件变量上界等待；忽略非回执行如 stats 行）。
- `DanmakuOutlet.__init__(spawn=None)` 注入口（默认 `_spawn_engine`，`stdout=PIPE`），
  供离线测试注入假 pipe。
- `skipped` 回执 → `"sent"`：SCHEMA status 闭集 `{sent, shown, accepted, rejected, done}`
  无"丢弃"值；`sent`=已投递未确认渲染，与 `shown`=确认渲染对齐（详见"需集成者同步"）。

## 命令与结果

| 命令 | 结果 |
|---|---|
| `python3 -m py_compile ...` | compile OK |
| `python3 tests/test_m5.py` | M5 smoke: ALL PASS（新增回执行全绿） |
| `for f in tests/test_m*.py tests/test_insight.py; do python3 $f; done` | m0–m5 + insight 全 PASS |
| `git diff --check` | whitespace OK |
| offscreen `Engine.push(..., mid=7/8)` | 输出 `{"id":7,"status":"shown"}` 等；无 id 行无回执 |
| offscreen 灌 15 条（9 lane） | 回执 15 条：shown=9 / skipped=6 |
| offscreen 真子进程 + `DanmakuOutlet` | render→`shown`（small+large），stats shown=2 |

## 证据

- `tests/test_m5.py`：`ReceiptReader` 乱序关联/非回执行忽略/超时 None；`DanmakuOutlet` 经
  `FakeProc` 验证 placed→shown、skipped→sent、无回执→sent 且上界 <1s。
- 引擎 offscreen 验证（`/tmp/eng_receipt_check.py`、`/tmp/eng_skip_check.py`、
  `/tmp/eng_integration_check.py`，一次性脚本，未入库）。

## 遗留 / 风险

- `skipped`→`sent` 的映射需集成者在 SCHEMA 注明（`sent`=已投递未确认渲染，含引擎丢弃）。
- 引擎冷启动 ~0.5s：首条弹幕可能因超时记为 `sent`（真实语义，非谎报）。
- `RECEIPT_TIMEOUT` 为类常量；如需按预算可调，留给集成者决定（当前未加配置项）。
- 未改 `docs/`（按派单排除）。
