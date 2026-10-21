# Backseat 运行手册（当前状态）

本文件是运维事实的唯一权威来源（current state）。变更即改写，不记录流水。

## 组件与依赖

| 组件 | 说明 |
|---|---|
| `python3 -m backseat` | 主守护进程：采集 → 见证 → 压缩 → KB → 人格 → 弹幕 |
| LiteLLM 代理 | `http://127.0.0.1:4000`（由用户会话持有，**非本项目托管**）。需 `LITELLM_BASE_URL` + `LITELLM_API_KEY` 环境变量 |
| 帧源 | 真实桌面（`$DISPLAY`）或合成源（Xvfb `:99` + mpv 循环播放视频） |
| 弹幕出口 | 由守护进程按需拉起 `python3 -m backseat.outlets.danmaku_engine`（stdin JSON-lines，PyQt5，点击穿透） |

系统依赖：ffmpeg、x11-utils（xprop/xdpyinfo）、python3-pyqt5、mpv（合成源）、Xvfb（合成源）。

## 启动与停止

```sh
# 合成数据源（无人值守/锁屏时用）：Xvfb :99 + 循环播放 ~/Videos/backseat-data/*.mp4
./scripts/dev-up.sh --synthetic

# 真实桌面（用户在机器前）：改 config.toml 去掉 display 或设为 $DISPLAY
./scripts/dev-up.sh

./scripts/dev-down.sh     # 停守护进程 + 视频源
```

守护进程读取仓库根 `config.toml`（存在即默认加载，可 `--config` 覆盖）。

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
```

## 验证

```sh
python3 -m backseat --stats                      # 调用/tokens/存储/消息层级汇总
tail -f ~/backseat-data/run.log                  # 运行日志（每分钟一行统计）
sqlite3 ~/backseat-data/backseat.db \
  "SELECT value FROM state WHERE key='witness_cursor'; SELECT count(*) FROM observations;"
sqlite3 ~/backseat-data/backseat.db \
  "SELECT value FROM state WHERE key='evict';"      # 最近一次驱逐水位（freed_bytes/evicted/used_after）
for f in tests/test_m0.py tests/test_m1.py tests/test_m2.py tests/test_m3.py tests/test_m4.py tests/test_m5.py tests/test_insight.py tests/test_evict.py tests/test_evals.py tests/test_bigref.py tests/test_freeze.py tests/test_witness_basis.py tests/test_d1_census.py tests/test_rehearse_writes.py; do python3 "$f" >/dev/null || echo "$f FAIL"; done
python3 scripts/freeze_snapshot.py               # 冻结当天快照（目标已存在需 --force）
python3 scripts/freeze_snapshot.py --verify      # 校验快照哈希（默认当天目录；改日需 --dir ~/backseat-data/snapshots/<UTC 日期>），不符退出 1
python3 scripts/freeze_snapshot.py --verify-frames  # 同上 + 帧哈希 + 覆盖度（被 small 行引用的帧都绑上了没）；判定绑在像素上
python3 scripts/freeze_snapshot.py --add-frames     # 给已有快照补 frames 段（不重做备份、不动库哈希）
python3 evals/run_eval.py --db ~/backseat-data/snapshots/<UTC 日期>/backseat.db --n 50  # 在冻结快照上跑评测尺子
# 重放复算（M0′ 的判据）：同一库跑两次，除报头时刻外必须逐项相同
python3 evals/run_eval.py --db ~/backseat-data/snapshots/<UTC 日期>/backseat.db --n 50 --json .tmp/e1.json
python3 evals/run_eval.py --db ~/backseat-data/snapshots/<UTC 日期>/backseat.db --n 50 --json .tmp/e2.json
python3 -c "import json;a,b=(json.load(open(p)) for p in ('.tmp/e1.json','.tmp/e2.json'));[d['meta'].pop('generated_at_utc') for d in (a,b)];print('replay identical:',a==b)"
python3 scripts/d1_census.py --db ~/backseat-data/snapshots/<UTC 日期>/backseat.db \
    --since-msg-id <冻结快照的 max small id>          # D1 A/C 普查（只读）：0 无缺陷 / 1 有缺陷 / 2 判定人口为空＝不能确立
# 预演：拿冻结快照的 small 正文、用现在的写入规则重写进临时库，再对它普查（不发模型请求）
python3 scripts/rehearse_writes.py --db ~/backseat-data/snapshots/<UTC 日期>/backseat.db --out /tmp/rehearse.db
python3 scripts/d1_census.py --db /tmp/rehearse.db    # 预演库每行都是新规则写的，故不带 --since-msg-id
sqlite3 -readonly ~/backseat-data/backseat.db \
  "SELECT count(*) FROM messages WHERE level='small' AND payload LIKE '%epithet_refused%';"  # 被拒称呼的行：模型没点名 cast，正文被删薄，但没静默沿用
grep -c "拒绝落行" ~/backseat-data/run.log          # 只剩称呼而整行被丢：这个数比拒绝数更疼
```

健康标准：`witness_cursor == count(observations)`（无积压）；`抓帧失败` 不持续增长；
`run.log` 无重复错误刷屏（同类错误 5 分钟最多一条）。称呼机制的模型侧读数不看内容而看两个计数：
`epithet_refused` 行数 / 含称呼行数 是"模型没点名"的比例，`拒绝落行` 是"整行只剩称呼"的损失；
前者高是提示词没教会 `cast`，后者高是规则在吃正文——两者都只记数，不据此改规则（规则只看依据在不在行内）。

## 生产判定人口（M0′ 的一次实跑）

修复后的判定人口只能由一次真实运行产出；三步都能单独复算，顺序是这个：

```sh
./scripts/dev-up.sh                     # 或 --synthetic：合成帧也进 observations，只是内容无意义
# 跑够 ≥200 行 small（B 的抽样与称呼拒绝率都要这个量），再：
python3 scripts/freeze_snapshot.py       # 冻结当天快照（默认已含 frames 段）
python3 scripts/d1_census.py --db ~/backseat-data/snapshots/<UTC 日期>/backseat.db --since-msg-id 25900
```

`--since-msg-id` 一律取**旧**冻结快照的 max small id（当前 25,900），不要换成"今天"：它划的是修复前后的缝，不是日期。判定人口非空之前退出码是 2（不能确立），不是通过。

## 数据与日志位置

| 路径 | 内容 |
|---|---|
| `~/backseat-data/backseat.db` | SQLite（WAL）：observations / messages / kb / expressions / metrics / state |
| `~/backseat-data/frames/YYYYMMDD/*.jpg` | 入选帧（1600px JPEG q6） |
| `~/backseat-data/trajectory/YYYY-MM-DD.jsonl` | 每次 LLM 调用全量（messages/响应/usage/时延） |
| `~/backseat-data/snapshots/<UTC 日期>/backseat.db` | 冻结快照（只读副本）+ `MANIFEST.json` + `backseat.db.sha256`；通道 A 的唯一输入 |
| `~/backseat-data/run.log` | 守护进程日志（启动时保留尾部 500KB） |
| `~/.litellm.log` | 代理日志（**会膨胀**，定期清理） |

## 模型路由现状

| 模型 | 状态 |
|---|---|
| `github_copilot/gpt-6-luna` | **当前选用**：约 8s/批调用、265 tok/帧；月配额，每月 1 日重置 |
| `volcengine/glm-5.3-flash` | 备用：可用但慢（~104s/批）且**周配额 + 5 小时滚动配额**双限制 |
| `zai-coding/glm-5.3-flash` | 用户 Coding Plan；需 litellm yml 里 `api_base: https://open.bigmodel.cn/api/coding/paas/v4`（按量端点会报余额不足） |
| `siemens/*`、`github_copilot/claude-*` | 路由不可用 |

## 已知坑（踩过的）

1. **litellm yml 是多会话共享文件**：并行 agent 改动可能破坏结构（缺 `model_name` → 代理启动即崩）或被还原。改前备份，改后用 `python3 -c "import yaml; yaml.safe_load(open('/home/z/litellm.yml'))"` 验证。
2. **代理不由本项目托管**：机器重启后可能不在（当前由用户会话启动）。本项目只依赖 `127.0.0.1:4000` 可达。
3. **后台任务输出有 20MB 上限**：守护进程必须把日志重定向到文件（`scripts/dev-up.sh` 已做）。
4. **mpv 状态行刷爆任务输出**：合成源必须 `--no-terminal` 且重定向到 `/dev/null`。
5. **会话锁屏后抓屏全黑**：真实桌面锁屏时无有效数据（黑帧被 `<40KB` 预过滤跳过）；此时用合成源。
6. **弹幕「shown」= 引擎确认落位**：出口给每条弹幕带自增 id，引擎逐条回执（落位成功=`shown` / 无空余 lane=`skipped`，一行 JSON）；出口按 id 关联后才推进 `expressions.status`。skipped / 等待超时（上界 0.5s）/ 旧引擎无回执一律记 `sent`（已投递未确认），写失败记 `failed`。
7. **`python3 -m backseat` 默认读取仓库根 config.toml**；若不存在则用内置默认（会看 `$DISPLAY`）。
8. **弹幕引擎 stdin EOF 不自退**：`scripts/dev-down.sh` 停守护进程后 `python3 -m backseat.outlets.danmaku_engine` 可能残留（pattern 不匹配引擎），需手动清理（`pkill -f backseat.outlets.danmaku_engine`）。
9. **守护进程意外死亡时引擎侧回执 `print` 可能抛未捕获 `BrokenPipeError`**：出口已死则 stdout 读端消失，引擎逐条回执写失败（正常关闭路径不受影响）。
10. **`freeze_snapshot.py --force` 重冻结是「先删旧再换名」**：中间有 kill 窗口；要保住旧副本就不要加 `--force`。
11. **`messages.parent_id` 的索引 `idx_msg_parent` 由 `Store.__init__` 的 `executescript` 自动补建**：真实库尚未行使，首次以写模式打开时建索引（秒级）。
12. **RUNBOOK 的测试清单是手写枚举**：新增 `tests/test_*.py` 必须同时加进这一行，否则不会被扫测。
13. **见证层会拒绝越帧称呼**：`浅色外套男子` 这类上一行用过的人物称呼，模型没在 `cast` 里点名本轮某张图（图号从 1 起）就在正文里被删掉，并记进该行 `payload.epithet_refused` 与 `witness.stats.epithet_refused`；删空的整行丢弃不落库。正文里称呼不见时先看这两处。

## 待校准/待办

- `persona_mode=assistant` 的洞察质量与频率，需要用户用真实使用数据打分（合成视频数据只能验证机制）
- 存储水位与驱逐：预算 `storage_bytes`（默认 20GB ≈ 3 周原始帧），超预算时每 60 tick 按 ts_wall FIFO 驱逐最旧非豁免帧（豁免 pin / 最近 anchor 引用帧 / 全分辨率副本），水位记于 `state.evict`；待校准 = 驱逐激进度与全分辨率副本配额是否匹配真实留存偏好
- 输入流可插拔（RTSP/图片序列/任意 Source）见 ROADMAP 2.1 Backlog
