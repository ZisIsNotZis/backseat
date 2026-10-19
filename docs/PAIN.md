# Backseat 痛点（文档链最上层）

本文件只记录谁疼、疼在哪、证据是什么、哪些痛不打算解决；产品、架构、方法与计划的判断都以它为前提。

## 一句话问题

系统要在人的工作里替人做事，却没有任何端到端数据能证明它做对了，而它最容易拿到数据的场景恰好是最不可能被接管的场景。

## 谁疼

- 用户本人：单人研究/工程工作者，全天与 agent 对话，多数时间处在 auto advance（离开机器、在做别的事）。【来源：用户自述】
- 疼点一：完全没有端到端数据，任何"它懂我"或"它帮上忙"的说法都无从检验。【来源：用户自述】
- 疼点二（矛盾）：最容易获得数据的场景，恰好是最不可能被接管的任务。【来源：用户自述】
- 用户的判断：用 agent 监督 agent 在开放研究任务上不可行，其 harness 约 90% 决策自动、10% 需人工 pilot。【来源：用户自述】
- 另有第三方报道给出更低比例，未核实，不作为证据。【来源：第三方报道，未能复核】

## 证据（本机现状）

- observations 42,396 条，sensor/kind 只有 screen/screen.notable 一种。【实测：sqlite3 -readonly ~/backseat-data/backseat.db "select sensor,kind,count(*) from observations group by 1,2"】
- messages 24,551 small / 1,227 medium / 122 big，expressions 366 条 shown。【实测：按 level 与 status 分组计数】
- 只有一条取证通道的数据，因此没有任何跨域对照，"理解对了"和"这条通道恰好够用"无法区分。【由前两条实测推出】
- 用户全天在 agent 会话里，人类行为因此稀疏，均匀采样必然大量采到无人的画面。【来源：用户自述，与 screen-only 数据一致】
- 数据流是单点依赖：见证游标停在 42,395 而 observations 已达 42,396，日志原因是模型端点 Connection refused。【实测：state.witness_cursor 与 count(observations)；~/backseat-data/run.log】
- 采集覆盖面还停在一个传感器上，GitHub 只是外部 observe 的一条通道，不是主观察路径。【来源：用户自述，与 screen-only 数据一致】
- 数据库 25.1MB 且 state 无 evict 键，说明存储预算从未触发过驱逐。【实测：ls -l ~/backseat-data/backseat.db 与 state 键集】

## 非目标（不打算解决的痛）

- 开放研究任务上的自动接管：人类 pilot 占比见上时不可行，只做人类 pilot 的辅助，不承诺接管；不声称接管的纪律住在 [ROADMAP.md](ROADMAP.md) 的非目标里。
- 没有既有 check 可判定的接管：无法证伪，只能弃权。
- 用户没有表达过的偏好：不可测，不进入画像。
- 按键内容与聊天对象的内容：不采集；痛点在"什么时候可以帮"，不在"知道用户说了什么"。
