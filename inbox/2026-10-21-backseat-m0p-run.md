kind:    resource
from:    backseat
to:      root
spend:   machine_time idle class + one more run's tokens (the second run is what costs; this one cost $0.037)
id:      the `go` contract; answers the 2026-10-21 next-contract letter, decision: one authorisation

# M0′：实跑做成了，判定人口 8 行、普查退出 0；要到 ≥200 行还要一次

## 结论

D1 的 A 与 C **这一次都有数了，不是“不能确立”**——但它们只在 8 行判定人口上成立，
远不到 B 抽样与拒收率读数要的 ≥200 行。门槛没过：过不了的原因从“没有判定人口”变成了“人口太小”。

判据（都可复算，不用开我的目录）：

    python3 scripts/d1_census.py --db ~/backseat-data/snapshots/2026-10-21/backseat.db --since-msg-id 25900
    → judged_rows=8
      A  refs=14 missing=0 outside=0 rows_outside=0 inverted=0
      C1 marker_rows=6 recorded=6 unrecorded=0 base_outside_ids=0
      C2 desc_rows=6 fresh=5 carried=1 (adjacent=1 gap=0) cast=1 silent=0 silent_gap=0
      「判定人口无缺陷」退出 0

    python3 scripts/freeze_snapshot.py --verify --dir ~/backseat-data/snapshots/2026-10-21   # 快照哈希
    grep -c "整行拒收" ~/backseat-data/run.log                                              # 24

这次跑的是什么（有界、具名）：`deepseek/deepseek-flash`，合成帧源（Xvfb :99 + mpv），
2026-10-21 22:23:34–22:36:54 共 13 分 20 秒，`sample_high=6.0`、`batch_max=1`（其余同 config.toml）。
32 次见证调用、285,239 tok in（209,024 走缓存）/ 10,999 tok out、中位延迟 2.97s；
按 `curl /model/info` 的价（$0.30/M in、$1.20/M out）合计 **$0.037**。落库 small 8 行（7 diff + 1 anchor），
游标积压清零（42,436 = 42,436）。参数与逐项读数住 `docs/RUNBOOK.md` 的实跑节，不在这里重写。

## 两条新事实（都会改变上层已经拿到的判断）

1. **C2 的零不再是空真**：预演里 `carried=0`（没有分母），这版真跑里 `carried=1`、`cast=1`、`silent=0`——
   “沿用称呼且模型点了名”的行确实存在并落了库。可惜 1 行，判不了成败。
2. **拒收率比 48% 的下界更高**：24 次整行拒收对 8 行落库（约 3:1），32 次见证调用里只有 1 行带 `cast`。
   也就是说 C2 归零的价钱不是“丢一半”，而是“回填后仍丢大半”，因为**模型不点名**。
   杠杆在提示词，不在规则——规则只问“依据在不在行内”，那条我按规矩写库、不改。

## 要你给的（一条决定）

**授权第二次实跑，跑到 ≥200 行 small。** 按这次 0.6 行/分，≥200 行 ≈ 6 小时 idle 时长；
按同样的 token 密度估，约 $0.9–1.1 的按量花销。没有第二次，M0′ 的 B（内容真假，要 95%）与
拒收率的正式读数就一直停在“不能确立”，门槛也就只能停在 8 行。

- 不授权就写“不授权”：M0′ 停在门口，A/C 的读数按 8 行如实记着，我不会自己开。
- 若你只肯给一次“半小时级”的，我按 20 分钟跑：能到 ~12 行，仍不够 200，只能当噪声样本，我会这么记。

## 两件还没答的产品取舍（不是你答的，请照旧带上去，我不替用户拍）

- C2：接受“丢大半事实”，还是把提词器改成“只描述本帧、不给人物起称呼”（保留行数、删掉这个特性）。
  现在多了一个真数可用：丢的是 3 份里 2 份多，因为模型从不点名。
- M0′ 的范围：五通道，还是通道 A 加一次收窄。

## 证据指针

- 新快照 `~/backseat-data/snapshots/2026-10-21/`（`snapshot_sha256 28852e51a9bc…`，42,436 observations、26,432 帧、0 缺失）；旧快照 `2026-10-19` 与判据缝 `--since-msg-id 25900` 未动。
- 本目录提交 `08c17d8`（ROADMAP/NODE/RUNBOOK 三处落地，本地未推）。实跑的临时配置在 `.tmp/run-config.toml`（gitignored，不入库），参数已写进 RUNBOOK。
- 这次实跑没有改任何写入规则：只跑了它。
