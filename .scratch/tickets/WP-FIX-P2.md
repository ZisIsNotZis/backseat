# WP-FIX-P2 —— 独立评审 3 项 P2 修复 + 重新验证 + 重启守护进程

- 建档/完成：2026-10-13T18:44:39+08:00（`date -Iseconds`）
- 基线 revision：`6f004f5`（master，守护进程旧代码在跑）
- **最终 revision：`7948434f7f55840fb741d6a2a6dea456fff99f7c`（master）@ 2026-10-13 18:40:56 +0800**
- 角色：worker（本仓库唯一写入者）
- 权限：写限 `/home/z/vibe/backseat`；重启本项目合成源守护进程。未动 `~/Videos` 源、未改用户 `config.toml`、未对 `~/backseat-data` 执行驱逐。

## 范围

- 改：`backseat/persona.py`、`backseat/store.py`、`docs/SCHEMA.md`、`tests/test_evict.py`、`tests/test_m5.py`。
- 不改：帧采集/入选、pin 策略、引擎布局/lane 分配、`config.toml`、`~/Videos`、DESIGN 语义。

## 三项修复

### P2-1 回执超时后迟到条目无界滞留 —— `backseat/persona.py`

- **缺陷**：`ReceiptReader._read_loop` 无条件 `self._receipts[mid]=status`；`wait()` 超时返回后无人再取该 id 的迟到回执，字典随超时次数单调增长（引擎冷启动 >0.5s 时首条必触发）。
- **修法**：单调 id + `wait(N)` 逐个同步等待 ⇒ `wait(N)` 一超时就记 `_abandoned_upto=N`（`persona.py:103,127-136`）；读线程对 `mid <= _abandoned_upto` 的迟到回执直接丢弃（`persona.py:122-123`）。字典上界 = 在途请求数（实际 1）。选择该法而非带上限 OrderedDict：无需额外容量常量，语义精确（已放弃 id 的回执确定无人取），O(1)。
- **用例**：`tests/test_m5.py:139-176`（`FeedStream` 可控假 stdout，20 次超时+迟到回执后 `_receipts=={}`、`_abandoned_upto==20`、新 id 仍能收到）。

### P2-2 同秒帧路径撞名击穿豁免 —— `backseat/store.py`

- **缺陷**：帧名秒级 `%H%M%S`（`sensors/screen.py:194`），同一 tick 大事件与采样用同一 `now` 抓帧 → 多行 observations 同 `ref`。旧 `evict_l0` 按行 unlink：在豁免行与普通行之间达标时可能删掉豁免行仍引用的文件（DESIGN §8 豁免失效），并让另一行留悬空 `ref`。
- **修法**：`evict_l0` 改路径级（`store.py:165-239`）——新增 `done_refs` 防同页重复处理（`:197,211`）；删除前查同 `ref` 所有行，任一豁免（pinned / anchor obs_ids / `ref_full IS NOT NULL`）即整条路径跳过（`:213-221`）；unlink 成功后把所有同 `ref` 行一并 `ref=NULL`、`bytes=0`、写 `meta.evicted_bytes`（`:227-235`，纳入 `_meta_of` 辅助 `:241`，与旧约定一致、无残留幻影字节）；`freed_bytes`/`evicted` 同一路径只计一次。计法已在 docstring 注明：共享路径时 `storage_used()` 本就重复累计，驱逐归零所有共享行，故 `used_after`（重算）可能比 `used_before-freed_bytes` 降得多——权威水位是 `used_after`。
- **用例**：`tests/test_evict.py:156-193` —— 5a 两行同 ref 其一为 anchor 引用 → 文件保留、两行 ref/bytes 不变；5b 均非豁免 → 删除一次、两行 `ref=NULL`、`freed_bytes==500` 只计一次、`used_after==0`、两行 `meta.evicted_bytes` 各 500。

### P2-3 SCHEMA status 闭集与代码不一致 —— `docs/SCHEMA.md`

- **缺陷**：`docs/SCHEMA.md` Expression 小节声明闭集 `{sent, shown, accepted, rejected, done}`，但 `persona.py` 经出口回执会写 `failed`（写失败），`skipped`/超时映射为 `sent`。
- **修法**：闭集补 `failed`；明确 `skipped`/超时/旧引擎无回执映射为 `sent`（`docs/SCHEMA.md:26-30`）。同步 `store.py:86` 表注释 `sent|shown|accepted|rejected|done|failed`。RUNBOOK 坑 #6（`:84`）原文已含 `failed` 与 "skipped/超时→sent"，措辞一致，未改。

## 命令与结果（revision `7948434`）

| 命令 | 结果 |
|---|---|
| `python3 -m py_compile backseat/store.py backseat/persona.py tests/test_evict.py tests/test_m5.py` | OK |
| `python3 tests/test_m0.py` | M0 smoke: ALL PASS |
| `python3 tests/test_m1.py` | M1 smoke: ALL PASS |
| `python3 tests/test_m2.py` | M2 smoke: ALL PASS |
| `python3 tests/test_m3.py` | M3 smoke: ALL PASS |
| `python3 tests/test_m4.py` | M4 smoke: ALL PASS |
| `python3 tests/test_m5.py` | M5 smoke: ALL PASS（含新增 `receipt bound OK`） |
| `python3 tests/test_evict.py` | M-evict smoke: ALL PASS（含两处新增 `evict shared-ref OK`） |
| `python3 tests/test_insight.py` | Insight smoke: ALL PASS |
| `git diff --check` | 干净（提交前） |

提交：`7948434`（5 文件，+136 −19）。`git status` 仅余先前未跟踪的 `WP-RUNTIME-VERIFY.md`。

## 守护进程重启到新 revision

- `./scripts/dev-down.sh` @ 18:40:5x：停守护/mpv/Xvfb；`ps` 确认无残留 `danmaku_engine` 孤儿（本就不存在；脚本不杀该 pattern）。
- `./scripts/dev-up.sh --synthetic` @ 18:41:11–18:41:15：
  - Xvfb `436762` @18:41:11；mpv `436769` @18:41:13；**守护进程 `436829` @18:41:15**（新代码，提交 18:40:56 之后）。
  - run.log 新 banner（行 3996-3997）：`L1 见证层启用：github_copilot/gpt-6-luna` / `backseat M0-M5: display=:99 data=/home/z/backseat-data tier=mid`。

## 重启后四项复验 @ 18:42–18:44

1. **首轮 L1 witness 仍含 [BIGs]/[MEDIUMs] 且描述当前活动（M3）✅**
   - 重启后首条见证调用 `ts=18:41:22 model=github_copilot/gpt-6-luna in=10469 out=127`。
   - system 前缀：`[KB命中行]`@487 `[BIGs]`@6889 `[MEDIUMs]`@8217 `[SMALLs]`@8636 → 记忆金字塔首轮即载入。
   - `[BIGs]` 示例："约1791431784–1791436370，场景为持续逐帧比对同一段音乐视频及其相邻画面；用户意图始终是追踪镜头切换、人物构图、景别与姿势变化…"；`[MEDIUMs]` 覆盖 `1791887396–1791887814` 同主题。
   - 首轮输出忠实描述当前合成源活动（相邻视频帧比对、左侧黑边、中央"×"标记），M3 达标。
2. **`DanmakuOutlet.render` 受控触发得真实 `shown` 且引擎回执可查 ✅**
   - `DISPLAY=:99` 默认 `DanmakuOutlet()`：`render(...)` → `shown`（两条），`stats={'shown':2,'push_fail':0,'skipped':0,'unconfirmed':0}`；引擎 pid `443898` `/proc/443898/fd/1 -> pipe:[39244167]`（stdout 确为 PIPE）。
   - 引擎原始回执抓取：`{"id": 7, "status": "shown"}`（解析一致）。受控脚本创建的引擎退出后成孤儿，已 `kill` 清理。
3. **`state.evict` 主循环挂点仍生效 ✅**
   - 临时 data-dir + `storage_bytes=60000` + `display=":99"`、卸载 LITELLM env（仅 L0+驱逐）、`python3 -m backseat --config <tmp> --data-dir <tmp> --once 61`。**未触碰 `~/backseat-data`。**
   - 日志：`[evict] 释放=0MB 驱逐=7 豁免=0 水位=0MB`；`state.evict = {"freed_bytes":380509,"evicted":7,"exempt":0,"missing_files":0,"used_before":429672,"used_after":49163}`（`used_after<=60000`）。
   - 行保留：id1-7 `ref=NULL bytes=0 meta={"evicted_bytes":…}`；id8-14 原样保留 ref/bytes。临时目录已清理。
4. **run.log 健康 + witness_cursor ✅**
   - 重启后 tick 行 `抓帧失败=0`（旧进程曾 9，未增长）；去 banner/tick 行后 `uniq -c` 无重复错误项（唯一 "失败" 匹配即 `抓帧失败=0`）。
   - `witness_cursor=36615 == count(observations)=36615`（无积压）。
   - `--stats`：`调用=23820 tokens: in=255678731 out=7392251 缓存命中率=3%`；`观察=36615 帧（2075MB）`。
   - 运行进程：守护 `436829`@18:41:15 + Xvfb `436762` + mpv `436769`，无引擎孤儿。

## 遗留 / 风险

- `freed_bytes` 与 `used_before-used_after` 在**多行共享同一路径**时会分叉（前者每路径一次、后者归零所有共享行的 bytes）；已在 `evict_l0` docstring 明确记录，`used_after` 为权威水位。这是多行同 ref 导致 `storage_used()` 既有重复累计的已知现象，超出本 WP 范围。
- 引擎 stdin EOF 不自退（`danmaku_engine.py` 不调 `close_feed()`）、`dev-down.sh` 不杀引擎 pattern —— 既有生命周期小缺陷，非本 WP 回归，未修（与 WP-RUNTIME-VERIFY §7 一致）。
- 未改用户 `config.toml` 既有键、未动 `~/Videos`。
