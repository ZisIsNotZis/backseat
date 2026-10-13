# WP-RUNTIME-VERIFY —— master 新代码运行态与 M3 重启验收

派发：M3「重启验收未做」的验收材料；运行态验证 WP1/WP2/WP3 三个合并修复。
权限：写限 `/home/z/vibe/backseat`；可重启本项目合成源守护进程（Xvfb :99 + mpv，非用户真实桌面）。
时间戳均为本机实数（`date -Iseconds`）。**未发现需修复的真缺陷 → 未改任何代码。**

## 0. 现状基线（重启前）@ 2026-10-13T18:31:33+08:00

- 版本：`6f004f5 工单回填整合提交 hash（WP-INTEGRATION）`（三修复合并提交在 18:27–18:30）
- 运行进程（**旧代码**，启动早于合并）：
  - `374873` `python3 -m backseat --data-dir /home/z/backseat-data` @ 18:13:11
  - `376471` 引擎 @ 18:13:38；`398625` 引擎 @ 18:26:51（均为旧引擎，无回执；父进程已死，ppid=1579）
  - `381532` Xvfb :99 @ 18:16:17；`381584` mpv 循环播放 @ 18:16:20
- `state`：`session_start_wall=1791438622.69`（= 2026-10-08 13:50:22，陈旧 ~5 天）；`evict` 键**不存在**；`witness_cursor=36577` == `count(observations)=36577`（无积压）。
- `--stats`：`调用=23785 tokens: in=255327730 out=7386713 缓存命中率=3%`；`观察=36577 帧（2073MB） 消息={'big':112,'medium':1121,'small':22425}`
- 陈旧洞察实证（run.log，旧代码）：`[persona:danmaku-jun] 沉默权（洞察：这一轮已经连续活跃 124.6 小时）`（124.5→124.7 递增刷屏）。
- WP1 旧语义实证：`expressions` 366 行 **全部 `status=shown`**（旧代码"写成功即 shown"，RUNBOOK 坑 #6）。
- litellm 代理 `http://127.0.0.1:4000/health/liveliness` → `"I'm alive!"`。

## 1. 重启 @ 18:32:16–18:32:28

- `./scripts/dev-down.sh` @ 18:32:16 停守护/ mpv/ Xvfb；但脚本**不杀** `python3 -m backseat.outlets.danmaku_engine`（pattern 不匹配），旧引擎 376471/398625 残留（ppid 已是 1579）→ 手工 `kill` 清理。
- `./scripts/dev-up.sh --synthetic` @ 18:32:24：Xvfb `410247` + mpv `410252` + 守护 `410322 python3 -m backseat --data-dir /home/z/backseat-data` @ **18:32:28**（新代码）。日志追加至 `~/backseat-data/run.log`（启动时保留尾部 500KB）。
- run.log 新 banner：`L1 见证层启用：github_copilot/gpt-6-luna` / `backseat M0-M5: display=:99 data=/home/z/backseat-data tier=mid`。

## 2. M3 重启验收（核心）✅ 达标

**首次 L1 见证调用**（trajectory 中重启后第一条）：
- `ts=18:32:54  purpose=witness:batch1  model=github_copilot/gpt-6-luna  in=10269 out=138`
- system 前缀长度 9219，各段位置：`[KB命中行]`@487 `[USER_MODEL]`@542 `[BIGs]`@6889 `[MEDIUMs]`@8217 `[SMALLs]`@8448 `[expressions尾部]`@9013 → **记忆金字塔（BIGs/MEDIUMs）在首次调用即已加载**。
- `[BIGs]` 示例（5 条历史纪要，最早覆盖 `1791409623–1791436370`）："约1791431784–1791436370，场景为持续逐帧比对同一段音乐视频及其相邻画面；用户意图始终是追踪镜头切换、人物构图、景别与姿势变化…"
- `[MEDIUMs]` 示例："约 1791886823–1791887314，用户逐帧比对一段带有'ever'字样和中央'×'标记的视频画面，关注镜头切换、景别、人物姿势及场景构图…"
- **首轮输出原文**（`response`）：
  `{"type":"diff","diff":"画面从浅灰地面上张开双臂的人物投影切换为蓝砖拱廊前浅色外套男子的中景；男子面向镜头张口，左侧黑边与中央"×"标记仍可见。","intent":"比较相邻画面的镜头与人物变化"}`
- 判读：**正确描述当前在干什么**——合成源正全屏循环播放 MV/剧集，见证层准确捕捉"相邻视频帧比对"这一活动，并复现画面特征（左侧黑边、中央"×"）。**M3「重启后第一轮答对用户在干什么」达标。**
- 说明：`cached=0` 为 `gpt-6-luna` 路由特性（重启后序列 cached 全程为 0），非缺陷。

## 3. WP3 运行验证（洞察会话起点）✅

- run.log 重启后（行 3994 起）`grep -c "连续活跃"` = **0**；旧刷屏 `124.5/124.6/124.7 小时` 不再出现。
- 只读对 `~/backseat-data/backseat.db` 跑 `InsightMiner`（强制 `insight_anomaly=True` 以走该分支）：
  - `_session_start(now=18:34:15)` = `1791886595.27` → **2026-10-13 18:16:35**（`dur_hours=0.29`），由 observations 时间线回溯（最近一段连续、上界 30min gap）得出；**不含**陈旧 `state.session_start_wall=1791438622.69`。
  - `candidates()` 返回 `[]`（无 ≥6h 的 `long_session`，无其它达阈值洞察）。
- 结论：陈旧"连续活跃 124.5 小时"已消除，重启/停机跨过 gap 自动自愈。

## 4. WP1 运行验证（弹幕出口回执）✅

- 代码：旧 `4435c28^:backseat/persona.py:98` `stdout=subprocess.DEVNULL` → 新 `backseat/persona.py:136` `stdout=subprocess.PIPE`（`_spawn_engine` 为守护进程唯一 spawn 入口）。
- 受控脚本（`DISPLAY=:99`，默认 `DanmakuOutlet()`，不灌爆守护进程）：
  - `render("WP1 回执验证：引擎确认落位","neutral",0.5)` → **`shown`**；引擎 pid `413958`，`/proc/413958/fd/1 -> pipe:[38691617]`（**stdout 确为 PIPE**）；`stats={'shown':1,...}`。
  - 反证旧语义（注入永不回执的假引擎 `DanmakuOutlet(spawn=...)`）：`render(...)` → **`sent`**，`stats={'unconfirmed':1}`。→ 无回执时不谎报 shown。
  - 引擎原始回执行抓取：`{"id": 7, "status": "shown"}`。
- 守护进程持久化路径：`persona.py:293-295` 先插 `status="sent"`，再 `status = self.outlet.render(...)` → `UPDATE expressions SET status=?`，即 DB 写的是出口真实回执。
- 注：本场景 `persona_mode=assistant` 且 `candidates()` 为空，守护进程按设计沉默（`on_digest` 无洞察即不开口），故运行期未产生新 `expressions` 行；回执语义已用真实引擎 + 反证覆盖。
- 受控脚本创建的两个引擎退出后成孤儿（见 §7 遗留），已 `kill` 清理。

## 5. WP2 运行验证（L0 存储驱逐）✅

- 方法：`mktemp -d` 临时 data-dir + 临时 config（`storage_bytes=60000`，`display=":99"`），**卸载 LITELLM env 使 L1 关闭**（仅 L0 + 驱逐挂点），`python3 -m backseat --config <tmp> --data-dir <tmp> --once 61`。**未对 `~/backseat-data` 执行驱逐。**
- 主循环挂点生效：`[evict] 释放=0MB 驱逐=2 豁免=0 水位=0MB`（tick=60）。
- 水位写入 `state.evict`：`{"ts_wall": 1791887848.92, "freed_bytes": 96139, "evicted": 2, "exempt": 0, "missing_files": 0, "used_before": 136067, "used_after": 39928}`。
- 行保留 + `meta.evicted_bytes` 完好：
  `id=1 ref=NULL bytes=0 meta={"evicted_bytes":45097}`；`id=2 ref=NULL bytes=0 meta={"evicted_bytes":51042}`；`id=3/4` 未驱逐保留 ref/bytes。
- 文件确被删：frames 目录文件数 61 → 59（删 2）；`missing_files=0`（unlink 成功，非"文件已缺失"）。
- 真实库 `~/backseat-data` `state.evict` 仍 **absent**（20GB 预算 vs ~2GB 用量，自然不触发，符合预期）。
- 验证后清理临时目录。

## 6. 健康检查 ✅ @ 18:37:56

- 重启后 run.log 无同类错误刷屏（去 banner/tick 行后 `uniq -c` 无重复项）；`抓帧失败=0`（旧进程为 9，未增长）。
- `witness_cursor == count(observations)`：`36595 == 36595`（无积压）。
- `python3 -m backseat --stats` 正常：`调用=23804 tokens: in=255509758 out=7389841 缓存命中率=3%`；`观察=36594 帧（2074MB）`。
- 运行进程：守护 `410322` @18:32:28（新代码）+ Xvfb `410247` + mpv `410252`。

## 7. 遗留 / 风险

- **代码改动：无**（未发现需修复的真缺陷；`git status` 仅新增本工单）。
- 观察（**超出本 WP 范围，未修**，供 Parent 决策）：`run_stdin_daemon`（`danmaku_engine.py:271-286`）在 stdin EOF 后不调用 `eng.close_feed()`，`Engine.tick` 的 `feed_closed` 永不置位 → 引擎不随守护进程退出而自我了结；`dev-down.sh` 的 pattern 也不杀引擎，故孤儿 `danmaku_engine` 会残留（重启前就有 2 个，我脚本又留下 2 个，均已清理）。属既有生命周期小缺陷，非三 WP 回归，不影响本次验收。
- LLM：验证期间守护进程按正常频率做 L1 见证调用（重启后 ~15 次 / 约 10K tok·次），未额外灌量。
- 未改用户 `config.toml`、未动 `~/Videos` 源文件。
