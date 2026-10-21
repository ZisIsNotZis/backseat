kind:    resource
spend:   tokens
from:    backseat
to:      root
id:      the `go` contract; answers the BS-M0P seal package's report (decision: none)

# backseat：M0′ 只差一次实跑；端点一直在跑，缺的是那一次的授权

milestone: **M0′ 数据基座与可评测性** —— 未到。挡着它的是两件，都在你/人手上：

1. **一次实跑**。D1 的 A/C 只能在"修复后落库的行"上判，而判定人口现在是空的（历史行
   不回填）。产这批判定人口要跑一次会调模型的产品。这是我这封信要的决定。
2. **scope 那一问**（`mail/2026-10-21-backseat-scope.md`，还没答）：M0′ 是五条通道，还是
   通道 A + 收窄。它决定 M0′ 的"五通道接入器与退出判据"那一行还算不算 M0′ 的交付。

command（两条就够看状态，不必开我的目录）:

    python3 scripts/d1_census.py --db ~/backseat-data/snapshots/2026-10-19/backseat.db --since-msg-id 25900
    → 退出 2，末行「判定人口为空：不能确立（不是通过）」

    python3 tests/test_witness_basis.py     → ALL PASS（136 条断言）

预演替你把"那一次实跑会得到什么"先算了一遍（同样的模型内容、现在的写入规则、不发模型请求）：

    python3 scripts/rehearse_writes.py --db /tmp/slice.db --out /tmp/rehearse.db && python3 scripts/d1_census.py --db /tmp/rehearse.db
    → A refs=4534 坏 0 出窗 0 倒置 0；C1 577/577 记明；C2 silent=0；退出 0

decision 要 root 给的（一条）：授权这次实跑，或者明确说不授权。

- 一次实跑 = `python3 scripts/backfill_epithets.py --db ~/backseat-data/backseat.db` +
  `./scripts/dev-up.sh`（合成帧源；Xvfb/mpv 与视频都在机器上，不需要人看着），跑 1–2 小时
  得 ≥100–200 行 small（观察到的速率 38–135 行/小时），然后 `python3 scripts/freeze_snapshot.py`
  与上面那条普查。花的是 `gpt-6-luna` 的月配额（10 月还剩 10 天），每帧约 265 out-token，
  一次实跑是几万 token 的量级，不是按量计费的钱。
- 不授权就写"不授权"：M0′ 停在门口，我不会自己开。我不替你判断，因为付配额的不是我。
- 一条更正：`mail/2026-10-21-backseat-proxy.md` 的前提过期了。`127.0.0.1:4000` 由用户的
  systemd 服务自 **2026-10-19 20:36** 起一直在跑（`ss -ltnp` 命中 litellm；带 key 的
  `/v1/models` 返回 200）；`run.log` 里那句 `Connection refused` 是 10-19 06:44，早于服务
  启动 13 个半小时。那封信问"起代理"，真正缺的是"跑一次"——建议撤信，换一句话问配额。

## 这一轮改了什么（`backseat/` 内本地提交，未推送；清单见 `git log 452f812~1..HEAD`）

1. `452f812` 封口的报告落进文档并删信：对比词面放宽到闭集表、称呼册累计、`payload.cast`。
2. `718d287` 基帧单步收窄：缺省基帧从"上一行整个 `obs_ids`"收成"上一行里最新的一帧"。
   封口模拟出原写法沿连续对比段累积（obs_ids p99=41、最长 69，落库最多 6）；现在 12 行
   连续对比段每行恒 2 个引用，把旧 `witness.py` 换回来同一夹具立刻红。
3. `a8794b2` 重放复算：同一快照两次 `run_eval` 的 JSON 除报头时刻外逐项相同，判据进了
   `tests/test_evals.py`（带"改一个字必须红"的对照）。门槛的"重放"一项已有证据。
4. `a4471ee` 预演（`scripts/rehearse_writes.py`；77 条断言）：把冻结快照的 small 正文交给
   现在的写入规则重算证据基并落库，不发模型请求。
5. `12ba961` 称呼册：库表 `epithets`（不封顶、跨重启仍在）、匹配与拒收同一套归一化、
   沿用未点名**整行拒收**（正文不改写）、历史回填（`scripts/backfill_epithets.py`，幂等）。
6. `e8152a0` / `6ce4b48` / `b60af2c` 文档跟上：SCHEMA 的落地规则、RUNBOOK 的实跑三步、
   NODE 的决定与预演读数。

预演在实跑之前抓到四个缺陷（都让门槛的红字落在错误的晚上）：基帧累积；词面匹配没做同一套
归一化（"穿米色大衣的男子"匹配不上册里的"米色大衣男子"）；册子封顶 200 而记录里有 15,368 个
词面（忘掉就静默沿用）；删名字的文本手术把"切换为"一起吃掉（标记行 1,682 → 1,671）。
前三条已修，第四条改成整行拒收。

## 代价（要你知道，不是要你决定）

整行拒收的价钱：预演切片 3,000 行里 1,439 行（48%）不落库，因为模型**从不**在 `cast` 里
点名（轨迹里 2,163 次见证调用，0 次带 cast）。这是 C2 归零的价钱，不是副作用；`run.log`
里 `整行拒收` 的行数就是它。要不要换个付法（改提词器、禁止给人物起称呼）见同目录的
`2026-10-21-backseat-c2-cost.md`（kind: direction，要 taste）。

## effort

- 想不清就量：封口把"要不要收窄基帧"留给我，我按它量到的累积（p99=41）自己拍了收窄——
  它是一步、不要模型之外的信息，不需要问任何人。
- 想过不用钱就把人口造出来：机器上有 4090 与 llama.cpp 的多模态权重，跑一次不花钱。不做，
  两个理由：独占 GPU 不在我这一轮的授权里；换模型写出的行会落进同一个人口，把"修复后的行"
  弄脏，B（内容真假）就没法判。
- 预演代替不了实跑：它不推进游标、不产新快照；门槛要的是后者。
- 没做的可选项：标题字段化（M0′ 可选增强）——它的消费者是 M1′ 的基线，而它要动 observations
  的写入路径，实跑之前不动写入面。理由与重开条件写在 `docs/NODE.md`。
- 本地提交未推送（推是 root 的权限；`git branch -r` 为空，远端引用没抓过）。
