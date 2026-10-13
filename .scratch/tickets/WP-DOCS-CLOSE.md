# WP-DOCS-CLOSE —— 仅文档收尾（SSOT 当前事实）

- 建档/完成：2026-10-13T18:50:40+08:00（`date -Iseconds`）
- 基线 revision：`7948434`（master，已通过独立评审）
- 角色：worker（本仓库唯一写入者）
- 权限：写限 `/home/z/vibe/backseat`；**未重启守护进程**、未改任何代码
- 性质：**纯文档增量**——仅补 SSOT「当前事实」，无代码改动

## 范围

- 改：`docs/ROADMAP.md`、`docs/RUNBOOK.md`
- 不改：任何 `backseat/**`、`tests/**`、`scripts/**`、`config.toml`、`~/backseat-data`
- 无 changelog/日期流水；日期仅作事实锚点，改写不追加

## 1. `docs/ROADMAP.md` 状态表（确认门列）

- **M3 记忆金字塔**：「重启验收未做」→ 已产出验收证据（2026-10-13 切至 `7948434` 重启后首轮 L1 见证调用 system 前缀含 `[USER_MODEL]/[BIGs]/[MEDIUMs]/[SMALLs]`，输出忠实描述当前活动），**待用户确认**。
  - 事实核验（只读 `~/backseat-data/trajectory/2026-10-13.jsonl`）：`7948434` 重启后首条 witness 记录 `ts=18:41:22 purpose=witness:batch1 model=github_copilot/gpt-6-luna`，system 段 `[USER_MODEL]`@542 `[BIGs]`@6889 `[MEDIUMs]`@8217 `[SMALLs]`@8636；response 忠实描述当前合成源活动。与 WP-RUNTIME-VERIFY / WP-FIX-P2 工单证据一致。
- **M4 唤起记忆**：保持「callback 演示未做」，注明原因（需真实桌面数据；合成视频数据下无意义）。
- **M1 传感器**：保持原句「实跑一天未正式验收（合成数据可代跑，需用户确认口径）」——已含所需口径说明，无改动。
- **M5 / M6**：保持「需用户」不变。
- 其余行不动。

## 2. `docs/RUNBOOK.md`「已知坑」补两条（评审残余项，当前事实）

- 新增 **#8**：弹幕引擎 stdin EOF 不自退——`scripts/dev-down.sh` 停守护进程后 `python3 -m backseat.outlets.danmaku_engine` 可能残留（`dev-down.sh` 的 pattern 不匹配引擎），需手动清理（`pkill -f backseat.outlets.danmaku_engine`）。
- 新增 **#9**：守护进程意外死亡时引擎侧回执 `print` 可能抛未捕获 `BrokenPipeError`（出口已死则 stdout 读端消失；正常关闭路径不受影响）。
- 代码核验：`danmaku_engine.py run_stdin_daemon`（`:271-286`）EOF 后不调 `close_feed()`；`_receipt`（`:177-182`）`print(..., flush=True)` 无 `try/except`；`dev-down.sh` 仅 `pkill -f "python3 -m backseat --data-dir"`，不匹配引擎。两条均为既有事实。

## 3. 一致性检查（Release 2.0 待校准/待办）

- `docs/RUNBOOK.md`「待校准/待办」已由集成者（WP-INTEGRATION）改写：无「渲染回执待补」、无「存储驱逐未实现」；存储驱逐改写为已实现 + 待校准项＝驱逐激进度/全分辨率副本配额。与 `7948434`（P2 修复）后的当前事实一致，**无需再改**。
- 集成者已同步 RUNBOOK 坑 #6（shown=引擎确认落位、failed/sent 语义）、验证段 `state.evict`、DESIGN §4/§8、SCHEMA Expression 回执；本轮仅补齐评审残余两条坑。

## 命令与结果

| 命令 | 结果 |
|---|---|
| `git diff --check` | 无输出（干净） |
| `grep`/`read` 核验两处风险代码 | 与文档描述一致 |

提交：见「最终 revision」。

## 最终 revision

- 本次提交：见 `git log --oneline -1`（`docs/ROADMAP.md` + `docs/RUNBOOK.md` + 本工单；**无代码改动**）

## 遗留 / 阻塞

- 无阻塞。
- M3 验收证据已就绪，**待用户确认**；M1/M4/M5/M6 各自确认门仍待用户在场。
