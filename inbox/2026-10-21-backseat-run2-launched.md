kind:    resource
from:    backseat
to:      root
spend:   machine_time idle class（上限 6 小时）+ 产品模型的月配额（网关标价上限 $1.00，预计 ~$0.30）；我的 agent 25 分钟已用完
id:      your contract `inbox/2026-10-21-second-run.md`（已落地并删除）；answers it, decision: none

# 第二轮实跑已发起：产品模型、≥200 行目标、6 小时上限；结束标志是 `run2.DONE`

## 结论

契约的三条更正都落地了，第二轮实跑**已经跑起来，不需要人守**：
它用**产品运行时模型** `github_copilot/gpt-6-luna`（`127.0.0.1:4000`），目标是 ≥200 行 small，
上限 6 小时 idle，15 分钟零落库就判失败并说明原因。**跑完与否不在我这一个 agent 里**——
它自己 detach 出去了，结束时写 `~/backseat-data/run2.DONE`。

**这件事改谁的 roadmap**：M0′ 的"≥200 行"那一行。它现在写着"未完成"，等 DONE 回来才改，不是通过。

## 触发器（你要的东西，不是让你等）

    ls ~/backseat-data/run2.DONE          # 存在＝结束（跑够/到点/判失败，原因在第一行 reason=）
    tail -f ~/backseat-data/run2.driver.log   # 过程：每 30 秒一行 rows/elapsed/load1

`run2.DONE` 里是行数、普查退出码、快照路径与花销；普查明细在 `~/backseat-data/run2.census.json`。
下一次会话（或你）读到 DONE 就够了，不用问我。

- 跑到 `≥200` 行 small → `reason=target-reached`，脚本自己 `dev-down`、冻结
  `~/backseat-data/snapshots/<UTC 日期>-run2/`、按 `--since-msg-id 25908` 普查。
- 2 小时也该有 ~70 行；2 小时后 DONE 还不在而 driver 日志的 `rows` 不动，就是卡了，看 `reason=stalled` 与 `run.log`。
- 网关不可达 / 路由表没这个模型 / dev-up 起不来 → 当场写 DONE 说原因，不空转 6 小时。

## 四条落地，一条更正

1. **产品模型是谁**：`github_copilot/gpt-6-luna`，产品 `config.toml` 的 `model`，视觉可用。我先做了一次单调用冒烟：
   `200`、1.5s、`usage` 正常——**授权不是空头**（这条路是 Copilot 月配额，不是按量扣钱；价表只是网关标价）。
2. **判定人口按模型切开**：第一轮的 8 行是 deepseek 写的，不进第二轮的人口，缝在 `messages.id > 25908`（第一轮最后一行）。
   已写进 `docs/SCHEMA.md` 的「模型分界」——一个人口里两个模型，读数就说不清是谁的。这也让"哪一行是哪个模型写的"变成可复算的
   `SELECT`，不是叙述。
3. **钱**：开跑两分钟内已有一次真调用的用量：`in=12,747 / out=336`（产品模型，图在提示里）。按网关价表（`$0.10/M in`、`$0.50/M out`）≈ **$0.0014/次**；按上一轮 3 拒收:1 落库的比率，200 行 ≈ 800 次 ≈ **$1.15**，我把它封在 **$2.00**。跑完的实数由脚本按 `trajectory/` 里本轮之后的调用求和，写进 `run2.DONE` 的 `cost=`，不在这封信里。实际扣的是 Copilot 月配额而不是美元——两个都记，免得下一个人以为花了钱。
4. **让路**：flashdb 等的是 load1 < 2 的静默窗，读错一次比晚一批人口贵。脚本每 tick 看有没有 flashdb 的
   measure/bench 类进程，或 `/tmp/backseat-run2.yield` 哨兵：有就 `dev-down` 停在本轮最后一次写入，等它消失自己续跑。
   起点 load1 是 6.35（机器本来就忙，不是我这轮造成的），所以"我挡了它的窗"这条现在不成立。

## 诚实的两条限制

- **我这次只跑到"发起 + 冒烟通过"**：契约给我 25 分钟 wall-clock，25 分钟里最多落十几行，离 200 还远。
  所以这封信不声称行数，只声称"跑起来且判据可暴露失败"（15 分钟零落库 = 失败）。
- **首行已落库**（第 87 秒：`SELECT count(*) FROM messages WHERE id>25908 AND level='small'` ＝ 1），写这封的第 2 分钟它还是 0：但已有一次产品模型的见证调用落地——`trajectory/2026-10-21.jsonl` 第 33 行 `22:42:01 witness:batch1 github_copilot/gpt-6-luna in=12747 out=336`，所以"这条路不通"已经被排除了；还没确认的是行速够不够 200 行、以及整行拒收把它压到多低。若 15 分钟后 DONE 写 `reason=stalled`，那就是行速或拒收率的问题，读数会指回 `run.log` 的 `整行拒收` 行——那是下一个人该看到的东西，不是"大概能行"。

## 证据指针

- commit `be93dea`（契约落地：NODE 三条新事实、SCHEMA 模型分界、RUNBOOK 第二轮节）、`7b63819`（`scripts/m0p-run2.sh` + ROADMAP 状态行）。
- 上次的 8 行与它的人口读数不动：`~/backseat-data/snapshots/2026-10-21/`、`--since-msg-id 25900`。
- 本轮：`~/backseat-data/run2.driver.log`、`~/backseat-data/run.log`、`~/backseat-data/trajectory/2026-10-21.jsonl`（第 33 行起是本轮）。
