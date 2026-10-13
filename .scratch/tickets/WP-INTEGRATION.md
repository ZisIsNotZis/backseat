# WP: WP-INTEGRATION —— 三 lane 合并入 master 并收口

- 建档：2026-10-13T18:28:39+08:00（`date -Iseconds`，检查点 run 内多次采样，最终提交时刻见「最终 revision」）
- 基线：f67ab74（master，clean）
- 角色：集成者（唯一写入者；写权限仅 `/home/z/vibe/backseat` 主工作区 = master）
- 状态：完成（合并 + provenance 修正 + SSOT 文档 + 全量测试通过）

## 合并的 commit / 分支

三个 lane 分支的 ref 在集成开始时已不存在（`git for-each-ref` 仅 master，worktree 已移除），
但 commit 对象在主仓库可达。以 SHA 校验后按 `git merge --no-ff <sha>` 合并，语义与合并分支等价。

| lane | 分支（已不存在） | commit | 改动文件 | 合并 commit |
|---|---|---|---|---|
| A wp-shown-receipt | `pi-subagents/worker-WP-wp-shown-receipt-status-shown-home-z-vibe-backseat-Backseat-git-worktree-cwd-8b98abf7-4c28e23-dbae-s0-t0` | 4435c28 | `.scratch/tickets/wp-shown-receipt.md`(A)、`backseat/outlets/danmaku_engine.py`、`backseat/persona.py`、`tests/test_m5.py` | a66ce7d |
| B wp-storage-evict | `pi-subagents/worker-WP-wp-storage-evict-L0-DESIGN-8-RUNBOOK-home-z-vibe-backseat-Backseat-git-worktr-ab3eeb2b-139d444-a4d3-s0-t0` | 3341d15 | `.scratch/tickets/wp-storage-evict.md`(A)、`backseat/__main__.py`、`backseat/store.py`、`tests/test_evict.py` | 0d9e923 |
| C wp-insight-session | `pi-subagents/worker-WP-wp-insight-session-124.5-session-home-z-vibe-backseat-Backseat-git-worktree-c-fad6b675-2594128-aac9-s0-t0` | d88ce35 | `.scratch/tickets/wp-insight-session.md`(A)、`backseat/insight.py`、`tests/test_insight.py` | de77a58 |

合并前校验：master clean；`git log --oneline -1` = f67ab74；三 commit 均以 f67ab74 为父；
`git diff-tree --name-status` 文件集合与任务书逐字一致；`git merge-base --is-ancestor f67ab74 <c>` 全通过。

## 冲突与解法

无冲突（三 lane 改动文件互不重叠），`ort` 策略三次自动合并成功。
合并后 `git merge-base --is-ancestor` 确认 4435c28 / 3341d15 / d88ce35 均在 master 历史中。
三个 lane 功能全部保留，无一边被覆盖。

## provenance 修正（B 的溯源缺口）

`Store.evict_l0()` 原先直接 `SET bytes=0`，丢失被驱逐帧的原始大小。改为无损：

- 置 0 前把原 `bytes` 写入该行 `meta` JSON 的 `evicted_bytes`（解析已有 meta；非 dict/坏 JSON 时降级为 `{}`）；
- `storage_used()` 语义不变（仍 `SUM(bytes)`＝预算水位），原始大小与「是否被驱逐」仍可查；
- `tests/test_evict.py` 新增断言：被驱逐行 `bytes==0` 且 `meta.evicted_bytes==原值`（场景 1 两条、场景 2 一条、场景 3 缺失文件那条），豁免行 meta 不动。

未做（明确排除）：不改驱逐顺序/豁免规则/水位口径；不引入 `evicted_at` 等额外字段。

## SSOT 文档改动清单（current-fact 改写，无流水）

- `docs/RUNBOOK.md`
  - 已知坑 #6：改为事实——shown = 引擎确认落位（引擎逐条回执：落位=shown / 无空余 lane=skipped），出口按 id 关联后推进 `expressions.status`；skipped/超时(0.5s 上界)/旧引擎无回执记 `sent`，写失败记 `failed`。
  - 验证：新增 `state.evict` 水位查询。
  - 待校准/待办：删除"渲染回执（shown 语义）待补"；"存储驱逐未实现"改写为已实现（预算 `storage_bytes` 默认 20GB、FIFO 驱逐最旧非豁免帧、水位记于 `state.evict`），余下待校准项＝驱逐激进度与全分辨率副本配额。
- `docs/DESIGN.md`
  - §8：补注驱逐保留观察行、仅 `ref` 置 NULL，原大小入 `meta.evicted_bytes`，`storage_used()` 仍只累加 `bytes`。
  - §4：新增「会话边界（洞察口径）」——会话起点由 observations 实际时间线推导（相邻观察间隔 > `gap_close_min` 断开），不依赖 `state` 持久化起点；全文无 `state.session_start_wall` 依赖描述。
- `docs/SCHEMA.md`
  - Expression 小节：补实际回执机制（引擎逐条回执 shown/skipped + 出口按 id 上界等待；仅 shown 算已渲染，回执不可得保持 `sent`）。

## 测试命令与结果

```
python3 tests/test_m0.py     -> M0 smoke: ALL PASS
python3 tests/test_m1.py     -> M1 smoke: ALL PASS
python3 tests/test_m2.py     -> M2 smoke: ALL PASS
python3 tests/test_m3.py     -> M3 smoke: ALL PASS
python3 tests/test_m4.py     -> M4 smoke: ALL PASS
python3 tests/test_m5.py     -> M5 smoke: ALL PASS
python3 tests/test_evict.py  -> M-evict smoke: ALL PASS
python3 tests/test_insight.py-> Insight smoke: ALL PASS
git diff --check             -> 无输出（无空白问题）
python3 -m py_compile backseat/store.py backseat/__main__.py backseat/insight.py backseat/persona.py backseat/outlets/danmaku_engine.py tests/test_evict.py tests/test_m5.py tests/test_insight.py -> OK
```

## 最终 revision

见本文件所在提交（`git log --oneline -1`，commit message：整合三 lane …）。

## 遗留 / 阻塞

- 无阻塞。lane 分支 ref 已消失属事实记录（合并以 SHA 完成，等价）。
- 未做：未在运行中的守护进程上实测驱逐/回执（避免干扰在跑进程）；仅离线测试覆盖。
- 体验层验证（真实弹幕上屏 shown、真实超预算驱逐日志）留待下次用户在场时的运行观察。
