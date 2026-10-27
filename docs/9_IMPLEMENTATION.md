# 9_IMPLEMENTATION — 实现层：跑起来、验、读数在哪

这一层是这台机器上的操作面：环境与依赖、装与停、参数、验证命令、读数位置、已知坑、工程纪律。
它以 `backseat/` 现有代码与 `scripts/` 现有脚本为前提，只写"怎么做"——为什么这么设计在
[6_ARCHITECTURE.md](6_ARCHITECTURE.md)，字段与表结构在 [7_INTERFACE.md](7_INTERFACE.md)，
写入规则在 [8_DETAIL.md](8_DETAIL.md)，而**判定门槛、阈值与里程碑结论一律不在这里**（见
[0_ROADMAP.md](0_ROADMAP.md)）。本文件是运维事实的唯一权威来源：变更即改写，不记流水。

# EXPLICIT

## 运行环境的两条用户侧前提

- **LiteLLM 代理由用户会话持有，不是本项目的托管物**：项目只依赖 `127.0.0.1:4000` 可达；
  它掉的时候由本节点自己拉起来（命令见下文「端点的读数与自起」），**不当成用户的待办**再发一封信。
  （来源：组件表、坑 2 与「端点的读数与自起」节）
- **帧源的两态由用户在不在机器前决定**：在场用真实桌面（`$DISPLAY`）；离席、锁屏、无人值守
  期间用合成源＝Xvfb `:99` + mpv 循环播放 `~/Videos/backseat-data/*.mp4`。他的使用形态是长时间离场
  （见 [1_PROBLEM.md](1_PROBLEM.md) 与 [4_USECASE.md](4_USECASE.md)）。

# CONFIRMED

- 【已确认 2026-09-28】产品模型换成 `github_copilot/gpt-6-luna`（effort=low），拼批后
  ~265 out tok/帧、p50 8s ——「模型路由现状」表里那一行的读数源自这次确认。
- 【已确认 2026-09-18】schema 六张表的字段定稿（三表合一 + metrics），**此后再改表要迁移** —— 见下文「工程纪律」。

# INFERRED

## 组件与依赖

| 组件 | 说明 |
|---|---|
| `python3 -m backseat` | 主守护进程：采集 → 见证 → 压缩 → KB → 人格 → 弹幕 |
| LiteLLM 代理 | `http://127.0.0.1:4000`（由用户会话持有，**非本项目托管**，掉了自己起，见「端点的读数与自起」）。需 `LITELLM_BASE_URL` + `LITELLM_API_KEY` 环境变量 |
| 帧源 | 真实桌面（`$DISPLAY`）或合成源（Xvfb `:99` + mpv 循环播放视频） |
| 弹幕出口 | 由守护进程按需拉起 `python3 -m backseat.outlets.danmaku_engine`（stdin JSON-lines，PyQt5，点击穿透） |

系统依赖：ffmpeg、x11-utils（xprop/xdpyinfo）、python3-pyqt5、mpv（合成源）、Xvfb（合成源）；Python 3.12+。

## 安装、部署、回滚

安装（Linux/X11，一次性）：

```sh
# 系统依赖：python3.12+ ffmpeg x11-utils(xprop/xdpyinfo) python3-pyqt5
./scripts/install.sh              # venv + systemd user service
systemctl --user set-environment LITELLM_BASE_URL=http://127.0.0.1:4000
systemctl --user set-environment LITELLM_API_KEY=sk-...
systemctl --user enable --now backseat
```

`scripts/install.sh` 做三件事：查系统依赖（缺 `ffmpeg`/`xprop`/`xdpyinfo`/PyQt5 或不在 X11 会话内即退出）、
建 `~/.local/backseat-venv`（`--system-site-packages`，PyQt5 走系统包，venv 只装本仓）、写
`~/.config/systemd/user/backseat.service`（`After=graphical-session.target`、`Restart=on-failure`、
`WantedBy=graphical-session.target`，API key 经环境注入而不写进 unit）。

两种运行形态，二选一，不要同时开：

- **systemd user service**（常驻）：上面那三行 `systemctl --user …`；服务名 `backseat`。
- **手动直跑**（调试/实跑）：`python3 -m backseat`（数据在 `~/backseat-data/`）；
  `python3 -m backseat --stats` 出计量汇总。开发环境的起停用 `scripts/dev-up.sh` / `scripts/dev-down.sh`（下节）。

回滚：停服务（`systemctl --user disable --now backseat` 或 `./scripts/dev-down.sh`），代码回到上一版提交，
然后按原样重启。**数据目录不回滚**：库、帧、`trajectory/` 是既成事实，回滚代码不会让它们退回；
要回到某个时点的读数，用那一天的冻结快照（`freeze_snapshot.py` 的只读副本 + sha256），不拿实时库改写。
代理不在回滚范围内（它不归本项目）。

## 启动与停止

```sh
# 合成数据源（无人值守/锁屏时用）：Xvfb :99 + 循环播放 ~/Videos/backseat-data/*.mp4
./scripts/dev-up.sh --synthetic

# 真实桌面（用户在机器前）：改 config.toml 去掉 display 或设为 $DISPLAY
./scripts/dev-up.sh

./scripts/dev-down.sh     # 停守护进程 + 视频源
```

守护进程读取仓库根 `config.toml`（存在即默认加载，可 `--config` 覆盖）。`dev-up.sh` 已把守护进程输出
重定向到 `~/backseat-data/run.log`（启动前保留尾部 500KB），mpv 走 `--no-terminal` 且输出进 `/dev/null`。
`dev-down.sh` 只停守护进程 / mpv / Xvfb，**不动 litellm 代理**。

## 配置（config.toml）

```toml
display = ":99"                    # 帧源；删除/置空 = 用 $DISPLAY
model = "github_copilot/gpt-6-luna"  # L1/L2/人格共用的模型
sample_high = 6.0                  # 高档采样间隔（秒）
batch_max = 1                      # 每处理窗最多入选帧（合成数据下压吞吐）
digest_min = 30.0                  # 人格周期 digest 间隔（分钟）——频率旋钮
persona_mode = "assistant"         # assistant=信息不对称优先；companion=氛围组
insight_anomaly = false            # 深夜/超长连续类洞察（合成数据下无意义）
min_roast_interval = 10.0          # 节流秒
grounding_min_interval = 300.0     # M0″ 取材料的最小间隔（秒）
# grounding_projects_root = "~/vibe"
# grounding_sessions_root = "~/.pi/agent/sessions"
```

全量参数（弹性预算 `storage_bytes` / `daily_requests` / `effort` 及其导出的采样档、压缩粒度、anchor 频率、
`gap_close_min`、`phash_hamming`、`reasoning_effort`、`base_url`、`api_key_env` …）见
[config.example.toml](../config.example.toml)：「所有参数都是弹性预算的导出值，不是魔法常量」。
预算→参数的约束关系见 [3_REQUIREMENT.md](3_REQUIREMENT.md)，预算到代码位置的映射见
[6_ARCHITECTURE.md](6_ARCHITECTURE.md)。`config.toml` 本身是 gitignored 的校准期本地文件。

## 验证

常规三件（活着、在动、没积压）：

```sh
python3 -m backseat --stats                      # 调用/tokens/存储/消息层级汇总
tail -f ~/backseat-data/run.log                  # 运行日志（每分钟一行统计）
sqlite3 ~/backseat-data/backseat.db \
  "SELECT value FROM state WHERE key='witness_cursor'; SELECT count(*) FROM observations;"
sqlite3 ~/backseat-data/backseat.db \
  "SELECT value FROM state WHERE key='evict';"      # 最近一次驱逐水位（freed_bytes/evicted/used_after）
```

健康标准：`witness_cursor == count(observations)`（无积压）；`抓帧失败` 不持续增长；
`run.log` 无重复错误刷屏（同类错误 5 分钟最多一条）。

测试：`tests/` 下 15 个文件逐个直跑，非零即打印 FAIL：

```sh
for f in tests/test_m0.py tests/test_m1.py tests/test_m2.py tests/test_m3.py tests/test_m4.py tests/test_m5.py tests/test_insight.py tests/test_evict.py tests/test_evals.py tests/test_bigref.py tests/test_freeze.py tests/test_witness_basis.py tests/test_d1_census.py tests/test_rehearse_writes.py tests/test_grounding.py; do python3 "$f" >/dev/null || echo "$f FAIL"; done
```

这一行是**手写枚举**：新增 `tests/test_*.py` 必须同时加进这一行，否则不会被扫测（坑 12）。

冻结快照的三个开关加一个补写：

```sh
python3 scripts/freeze_snapshot.py               # 冻结当天快照（目标已存在需 --force）
python3 scripts/freeze_snapshot.py --verify      # 校验快照哈希（默认当天目录；改日需 --dir ~/backseat-data/snapshots/<UTC 日期>），不符退出 1
python3 scripts/freeze_snapshot.py --verify-frames  # 同上 + 帧哈希 + 覆盖度（被 small 行引用的帧都绑上了没）；判定绑在像素上
python3 scripts/freeze_snapshot.py --add-frames     # 给已有快照补 frames 段（不重做备份、不动库哈希）
```

评测尺子与重放复算（M0′ 的判据；门槛值归 [0_ROADMAP.md](0_ROADMAP.md)）：

```sh
python3 evals/run_eval.py --db ~/backseat-data/snapshots/<UTC 日期>/backseat.db --n 50  # 在冻结快照上跑评测尺子
# 重放复算：同一库跑两次，除报头时刻外必须逐项相同
python3 evals/run_eval.py --db ~/backseat-data/snapshots/<UTC 日期>/backseat.db --n 50 --json .tmp/e1.json
python3 evals/run_eval.py --db ~/backseat-data/snapshots/<UTC 日期>/backseat.db --n 50 --json .tmp/e2.json
python3 -c "import json;a,b=(json.load(open(p)) for p in ('.tmp/e1.json','.tmp/e2.json'));[d['meta'].pop('generated_at_utc') for d in (a,b)];print('replay identical:',a==b)"
```

D1 普查（只读，命令与退出码住这里，各分项**测什么**在 [2_GOAL.md](2_GOAL.md)、判定人口与阈值在 [0_ROADMAP.md](0_ROADMAP.md)）：

```sh
python3 scripts/d1_census.py --db ~/backseat-data/snapshots/<UTC 日期>/backseat.db \
    --since-msg-id <冻结快照的 max small id>          # D1 A/C 普查（只读）：0 无缺陷 / 1 有缺陷 / 2 判定人口为空＝不能确立
```

退出码语义：**0** 判定人口无缺陷；**1** 判定人口有缺陷；**2** 判定人口为空、C1 或 C2 的分母为 0（空真）、
库不可读或参数误用。`--json PATH` 把整份报告（逐项分母/分子/缺陷行）写到该路径。
`--since-msg-id` 一律取**旧**冻结快照的 max small id，不要换成"今天"：它划的是修复前后的缝，不是日期。
判定人口非空之前退出码是 2（不能确立），不是通过。一个人口只由一种运行时模型写出的行组成，换模型写出的行不混进来
（模型分界与人口的取值见下节与 [0_ROADMAP.md](0_ROADMAP.md)）。

预演（拿冻结快照的 small 正文、用现在的写入规则重写进临时库，再对它普查；**不发模型请求**）：

```sh
python3 scripts/rehearse_writes.py --db ~/backseat-data/snapshots/<UTC 日期>/backseat.db --out /tmp/rehearse.db          # 全量
python3 scripts/rehearse_writes.py --db ~/backseat-data/snapshots/<UTC 日期>/backseat.db --out /tmp/rehearse.db --limit 3000  # 切片，报告里的数字用它复算
python3 scripts/d1_census.py --db /tmp/rehearse.db    # 预演库每行都是新规则写的，故不带 --since-msg-id
```

预演库的册子从空表起、没先回填历史词面，所以它的拒绝率是**下界**、`carried` 可能为 0（C2 于是退出 2）。

称呼册回填与两个计数：

```sh
python3 scripts/backfill_epithets.py --db ~/backseat-data/backseat.db   # 幂等，就地写 epithets 表
grep -c "整行拒收：" ~/backseat-data/run.log    # 沿用称呼没点名 → 整行没落库 = C2 归零的价钱（带冒号：不带会把「丢弃 1 帧」那行也算一遍，翻倍）
sqlite3 -readonly ~/backseat-data/backseat.db \
  "SELECT count(*) FROM epithets;"                   # 称呼册的词面数（历史回填 + 新写入，不封顶）
```

称呼机制的模型侧读数只看一个计数：`run.log` 里 `整行拒收` 的行数 / 该时段写入的 small 行数——
模型没在 `cast` 里点名本轮某张图的行，整行不落库（正文不改写）。**这个 0 不等于模型没填**：
`trajectory/` 里的 `response` 是转义过的 JSON 字符串，`grep -c '"cast"'` 永远命中不了，
要用 `grep -c '\\"cast\\"'` 或 python 解一下。

### 端点（`127.0.0.1:4000`）的读数与自起

断言"端点在不在"只读端口，不读进程表（它由用户会话启动，主机视角看不见不代表不在）：

```sh
ss -ltnp | grep :4000                                    # 哪个 pid 在听
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:4000/health/liveliness   # 200 = 活
ps -o lstart=,etime= -p <上面那个 pid>                    # 从什么时候起就在听
```

不在就自己起（见 EXPLICIT 的第一条），不等人：

```sh
nohup ~/.venv/bin/litellm --config ~/litellm.yml >> ~/backseat-data/run.log 2>&1 &
ss -ltnp | grep :4000        # 起来之后再读一次，别提前宣布
```

读数带三样才能引用：**读到的时刻**、**读的哪一面**（端口 / 进程 / 容器 / 服务）、**复算命令**。
"端点不在"与"进程表里没有""容器列表里没有"是三个断言，各自的表面不同。

## 生产判定人口（M0′ 的一次实跑）

修复后的判定人口只能由一次真实运行产出；三步都能单独复算，顺序是这个：

```sh
python3 scripts/backfill_epithets.py --db ~/backseat-data/backseat.db   # 称呼册先拿历史回填（幂等）
./scripts/dev-up.sh                     # 或 --synthetic：合成帧也进 observations，只是内容无意义
# 跑够 B 抽样与称呼拒绝率所需的行数（要多少行归 0_ROADMAP.md），再：
python3 scripts/freeze_snapshot.py       # 冻结当天快照（默认已含 frames 段）
python3 scripts/d1_census.py --db ~/backseat-data/snapshots/<UTC 日期>/backseat.db --since-msg-id 25900
```

回填不能省：C2 的"沿用"是跨全库比的，不回填的话修复后第一次重写历史里的名字就不在册里，写出去就是静默沿用。
代价是这些行被整行拒收（模型从不在 `cast` 里点名）；这是"丢掉被沿用的那类行"换"C2 归零"的价钱，不是副作用，
取舍与读数归 [0_ROADMAP.md](0_ROADMAP.md)。

实跑用一份临时配置（参数改在文件里，不靠改 `config.toml`）：帧源那两条照 `dev-up.sh` 起，守护进程用
`python3 -m backseat --data-dir ~/backseat-data --config <那份临时配置>`；停是 `./scripts/dev-down.sh`
然后 `freeze_snapshot.py`。

读过的那几轮不用重跑也能复算：一轮的读数从当天冻结快照 + `run.log` + `trajectory/<日期>.jsonl` 求和复算，
普查的逐项在 `--json` 的产物里；第二轮那 1 行缺陷的机理只需快照与 `trajectory/2026-10-22.jsonl` 的第 201 行，
不需要再跑模型。数值与结论不复述，见 [0_ROADMAP.md](0_ROADMAP.md) 的 M0′ 状态行。

判定人口的切分由 `messages.id` 划：第一轮（`deepseek/deepseek-flash`）与第二轮（产品模型
`github_copilot/gpt-6-luna`）之间以 `messages.id > 25908` 切分，`25908` 是第一轮的最后一行。

### 第二轮：产品模型，跑到样（`scripts/m0p-run2.sh`）

```sh
setsid nohup sh scripts/m0p-run2.sh >/dev/null 2>&1 < /dev/null &   # 自 detach；立刻返回
ls ~/backseat-data/run2.DONE || tail -f ~/backseat-data/run2.driver.log
```

它自己做完五步，不需要人守：`backfill_epithets.py`（幂等）→ `dev-up.sh`（Xvfb + mpv + 守护进程，
读仓库 `config.toml`＝产品模型）→ 每 30 秒看落库行数 → 到行数目标或时间上限 → `dev-down.sh` →
冻结 `~/backseat-data/snapshots/<UTC 日期>-run2/` → 用 `--since-msg-id 25908` 普查 → 写 `run2.DONE`。
三个停止点都是脚本自己的常量（`TARGET_ROWS` / `MAX_SECONDS` / `STALL_SECONDS`，值住脚本），
它们要多少行、怎么算失败归 [0_ROADMAP.md](0_ROADMAP.md)。

| 触发/读数 | 位置 |
|---|---|
| 跑完、跑停、或判失败 | `~/backseat-data/run2.DONE`（存在即结束；内容是行数、普查退出码、花销、原因） |
| 过程 | `~/backseat-data/run2.driver.log`（每 30 秒一行：行数、已耗时、load1） |
| 提前停 | `touch /tmp/backseat-run2.yield`（脚本 30 秒内 `dev-down`，哨兵消失后自己续跑） |
| 判失败的两种 | 15 分钟零落库（`STALL_SECONDS=900`）或网关 `/v1/models` 非 200：写 `run2.DONE` 并说原因，不空转到 6 小时 |
| 让路 | 每 tick 看 `pgrep -af flashdb` 有没有 measure/bench 类进程（flashdb 的静默窗）；有就停在本轮最后一次写入，等它消失再续 |

预检两关（网关 200、该模型在路由表里）不过就当场 `finish`，不空转。零行时不冻结快照——冻结一个没有新行的库
只会让人以为跑过了。花销按 `trajectory/` 里起点之后的 usage 求和、乘网关价表算，取不到就记"缺席"，不记 0。

## M0″ 外部材料链：演示 / 判官 / 抽样

端点与模型事实（未在本机设置环境变量）：`http://127.0.0.1:4000` 监听中；`LITELLM_BASE_URL` /
`LITELLM_API_KEY` **不在环境里**，仓库根也**没有 `config.toml`**，所以脚本走显式参数
`--base-url` / `--api-key`；默认模型 `github_copilot/gpt-6-luna`。`--no-model` 是确定性出口（只取材料，
然后 `model_disabled` 沉默），不伪造评论。

```sh
# 单条：真实仓库 + 真实 pi 会话 → 一条带引用的表达（或沉默）
python3 scripts/m0pp_demo.py --title 'zsh · ~/vibe/backseat' --data-dir ~/backseat-data
# 无模型（确定性）：材料照取，评论沉默
python3 scripts/m0pp_demo.py --title 'zsh · ~/vibe/backseat' --data-dir /tmp/m0pp --no-model
# 批量：最近 N 条真实用户消息（来自 ~/.pi/agent/sessions/--home-z-vibe-<项目>--/），每条一个决策
python3 scripts/m0pp_demo.py --moments 50 --data-dir ~/backseat-data

# 判官：有据率 / 无材料出口 / 错题 / 沉默原因（退出码 0 全绿、1 不绿、2 无决策）
python3 scripts/m0pp_eval.py --decisions ~/backseat-data/grounding
# 人那门：抽样表（正确/错误列留空，由用户填；题面正确率只在给 --labels 时算）
python3 scripts/m0pp_sample.py --decisions ~/backseat-data/grounding \
    --out ~/backseat-data/grounding/sample.md          # 默认可读版（只有 locator）
python3 scripts/m0pp_sample.py --decisions ~/backseat-data/grounding \
    --out /tmp/sample-full.md --full                    # 详细版（带材料正文）
python3 scripts/m0pp_eval.py --decisions ~/backseat-data/grounding \
    --labels ~/backseat-data/grounding/labels.json
```

决策日志是判官的唯一输入：`~/backseat-data/grounding/decisions-YYYY-MM-DD.jsonl`（形状见
[7_INTERFACE.md](7_INTERFACE.md)）。没有 `~/backseat-data` 时脚本按需创建；`--data-dir` 不给时 demo
用临时目录并在结尾打印路径。测试不依赖本机仓库：`python3 tests/test_grounding.py` 用临时 git 项目与临时
会话目录，全离线。

守护进程挂点：`Config.grounding_min_interval`（默认 300 秒）限制两次取材料的间隔；处理过的新 small 行由
`state.grounding_cursor` 游标推进，不重复触发；标题先扫该行全部 obs id，再退到该行之前 30 分钟内最近的
`meta.to` 观察，仍无标题就放弃（不调模型）；模型不可用时整条链不启用，L0/L1 不受影响。

## 数据与日志位置

| 路径 | 内容 |
|---|---|
| `~/backseat-data/backseat.db` | SQLite（WAL）：observations / messages / kb / expressions / metrics / state |
| `~/backseat-data/frames/YYYYMMDD/*.jpg` | 入选帧（1600px JPEG q6） |
| `~/backseat-data/trajectory/YYYY-MM-DD.jsonl` | 每次 LLM 调用全量（messages/响应/usage/时延） |
| `~/backseat-data/grounding/decisions-YYYY-MM-DD.jsonl` | M0″ 每个尝试（说/沉默）的决策日志，判官输入；`sample.md`/`labels.json` 是同目录的人工产物 |
| `~/backseat-data/snapshots/<UTC 日期>/backseat.db` | 冻结快照（只读副本）+ `MANIFEST.json` + `backseat.db.sha256`；通道 A 的唯一输入 |
| `~/backseat-data/run.log` | 守护进程日志（启动时保留尾部 500KB） |
| `~/.litellm.log` | 代理日志（**会膨胀**，定期清理） |

第二轮另外落三个产物在 `~/backseat-data/`：`run2.DONE` / `run2.census.json` / `run2.driver.log`（见「第二轮」的读数位置表）。
第 7 张表 `epithets` 由 [backseat/witness.py](../backseat/witness.py) 建、不归 store 管。

## 模型路由现状

| 模型 | 状态 |
|---|---|
| `github_copilot/gpt-6-luna` | **产品选用**（config.toml 的 `model`，第二轮实跑用它）：约 8s/批调用、265 tok/帧；视觉可用；月配额，每月 1 日重置。网关价表（`curl -H "Authorization: Bearer $LITELLM_API_KEY" 127.0.0.1:4000/model/info`）：$0.10/M in、$0.50/M out——这是路由上的标价，实际扣的是 Copilot 月配额 |
| `deepseek/deepseek-flash` | **实跑用的便宜运行时模型**：视觉可用（图走 `image_url` data URL）、2.97s/批（32 次调用中位数，max 5.1s）、8987 tok in / 378 tok out 每批；按量：$0.30/M in、$1.20/M out、$0.006/M cache-read（价格源 `curl /model/info`） |
| `volcengine/glm-5.3-flash` | 备用：可用但慢（~104s/批）且**周配额 + 5 小时滚动配额**双限制 |
| `zai-coding/glm-5.3-flash` | 用户 Coding Plan；需 litellm yml 里 `api_base: https://open.bigmodel.cn/api/coding/paas/v4`（按量端点会报余额不足） |
| `siemens/*`、`github_copilot/claude-*` | 路由不可用 |

## 已知坑（踩过的）

1. **litellm yml 是多会话共享文件**：并行 agent 改动可能破坏结构（缺 `model_name` → 代理启动即崩）或被还原。改前备份，改后用 `python3 -c "import yaml; yaml.safe_load(open('/home/z/litellm.yml'))"` 验证。
2. **代理不由本项目托管**：机器重启后可能不在（当前由用户会话启动）。本项目只依赖 `127.0.0.1:4000` 可达；不在就按「端点的读数与自起」拉起来，不是待办。
3. **后台任务输出有 20MB 上限**：守护进程必须把日志重定向到文件（`scripts/dev-up.sh` 已做）。
4. **mpv 状态行刷爆任务输出**：合成源必须 `--no-terminal` 且重定向到 `/dev/null`。
5. **会话锁屏后抓屏全黑**：真实桌面锁屏时无有效数据（黑帧被 `<40KB` 预过滤跳过）；此时用合成源。
6. **弹幕「shown」= 引擎确认落位**：出口给每条弹幕带自增 id，引擎逐条回执（落位成功=`shown` / 无空余 lane=`skipped`，一行 JSON）；出口按 id 关联后才推进 `expressions.status`。skipped / 等待超时（上界 0.5s）/ 旧引擎无回执一律记 `sent`（已投递未确认），写失败记 `failed`。
7. **`python3 -m backseat` 默认读取仓库根 config.toml**；若不存在则用内置默认（会看 `$DISPLAY`）。
8. **弹幕引擎 stdin EOF 不自退**：`scripts/dev-down.sh` 停守护进程后 `python3 -m backseat.outlets.danmaku_engine` 可能残留（pattern 不匹配引擎），需手动清理（`pkill -f backseat.outlets.danmaku_engine`）。
9. **守护进程意外死亡时引擎侧回执 `print` 可能抛未捕获 `BrokenPipeError`**：出口已死则 stdout 读端消失，引擎逐条回执写失败（正常关闭路径不受影响）。
10. **`freeze_snapshot.py --force` 重冻结是「先删旧再换名」**：中间有 kill 窗口；要保住旧副本就不要加 `--force`。
11. **`messages.parent_id` 的索引 `idx_msg_parent` 由 `Store.__init__` 的 `executescript` 自动补建**：真实库尚未行使，首次以写模式打开时建索引（秒级）。
12. **本文件的测试清单是手写枚举**：新增 `tests/test_*.py` 必须同时加进「验证」里那一行，否则不会被扫测。
13. **见证层会整行拒收越帧称呼**：`浅色外套男子` 这类册里已有人物称呼，模型没在 `cast` 里点名本轮某张图（图号从 1 起）就**整行不落库**（正文一个字节不改），计数在 `witness.stats.epithet_refused`、日志是一行 `整行拒收`；`payload` 里没有 `epithet_refused` 这个键（行不存在）。一行事实凭空不见了，先看日志这一行。

## 待校准

- `persona_mode=assistant` 的洞察质量与频率，需要用户用真实使用数据打分（合成视频数据只能验证机制）。
- 存储水位与驱逐：预算 `storage_bytes`（默认 20GB ≈ 3 周原始帧），超预算时每 60 tick 按 `ts_wall` FIFO
  驱逐最旧非豁免帧（豁免 pin / 最近 anchor 引用帧 / 全分辨率副本），水位记于 `state.evict`；
  待校准 = 驱逐激进度与全分辨率副本配额是否匹配真实留存偏好。算法细节见 [8_DETAIL.md](8_DETAIL.md)，
  预算侧见 [3_REQUIREMENT.md](3_REQUIREMENT.md)。
- 输入流可插拔（传感器源不写死屏幕）归 [0_ROADMAP.md](0_ROADMAP.md) 的 Backlog，未实现。

## 测试策略

- 每个里程碑一个冒烟测试 `tests/test_mN.py`，风格是**无依赖直跑脚本**（`python3 tests/test_mX.py`，非零即失败）；
  没有测试框架、没有 conftest，谁都能在裸机上跑。
- 判据要落成检查，而不是落成一句话：重放复算这条判据已被 `tests/test_evals.py` 固定成检查，
  并带"改一个字必须红"的判别力对照；写入规则（批窗口 min/max、被切离端并入、越帧称呼整行拒收）
  由 `tests/test_witness_basis.py` 断言，它在旧代码上是红的（判别力对照：把 `4668493` 的 `backseat/witness.py` 拷进测试夹具，
  `epithet`/`markers`/`memory`/`cast`/`bound` 五节红，`batch`/`switch` 两节绿——后者是故意的，修复没搬动批窗口与 `since` 点名的语义）。
- 每个脚本自己也带可复算的自检面：`freeze_snapshot.py --verify` / `--verify-frames` 用哈希与覆盖度判，
  `d1_census.py` 用退出码判（0/1/2），都只读、不改库。
- 测试清单是手写枚举（坑 12），新增测试文件要同时进那一行。

## 工程纪律与提交规范

纪律（来自 [CONTRIBUTING.md](../CONTRIBUTING.md)）：

- **事实归引擎，风格归人格**：见证层/压缩层只产出事实；任何"怎么说"都是 Persona 插件。
- **确定性算法能做的，模型不碰**：pHash 门控、去重、折叠是纯代码；LLM 只出现在 L1-L3 的进场线上。
- **无魔法数**：运行参数都是弹性预算（`storage_bytes` / `daily_requests` / `effort`）的导出值。
- **表结构描述引擎本体**：插件不是表，是字段值（sensor / channel / persona id）。
- 新行为先进 [6_ARCHITECTURE.md](6_ARCHITECTURE.md) 的决策账本（Grill Round），再实现。
- schema 改动 = 迁移（字段定稿后的约定见 CONFIRMED 第二条）；语义契约改动 = 更新 [7_INTERFACE.md](7_INTERFACE.md)。
- **LLM 调用必须落 `trajectory/` JSONL + `metrics` 表**（成本可见性是产品功能，不是调试日志）。

提交：

- 一个 commit 一个完整关注点，注明实测证据（测试输出/成本数据）。
- AGPL-3.0；移植代码须同源同许可（弹幕引擎移植自同作者 danmaku 项目）。
