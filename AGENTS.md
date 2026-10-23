# backseat 入口

**这是给本节点下一次会话看的**，不是给上一层看的。`backseat` 是一个本机优先的 AI 长期伴随程序（屏幕感知 → 见证层 JSON 事实 → 记忆金字塔 → 人格层 → 弹幕出口），文档与代码都是中文注释；本目录同时是持有它 roadmap 的那个节点。

## 怎么开始

1. 读 [`docs/NODE.md`](docs/NODE.md)：这个节点为什么存在、roadmap 上有哪些工作包、当前决定过什么。
2. 读 [`docs/ROADMAP.md`](docs/ROADMAP.md) 的 **M0′** 一节：要交的里程碑、门槛、判定人口、状态行。（2026-10-23 起靶子已换成 **M0″**：题面→外部材料→评论，见该节开头的方向变更块。）
3. 碰传感器/换终端/改采集之前先读 [`docs/SENSORS.md`](docs/SENSORS.md)：那里面是“这条通道到底能拿到什么”的读数，不是设计愿望。
4. 读 `inbox/` 与 `mail/`（若有）：每封信是 `kind/from/to/spend` 开头，读完先落地到 `docs/`，再删信、再提交。
   收信与发信都走 `inbox/`：上一层的契约落在 `inbox/<日期>-*.md`，我给 root 的报告也写在那里（`to: root`），发完不留在 `mail/`。
5. 跑一遍测试（14 个文件，约 20 秒）：

```sh
for f in tests/test_m0.py tests/test_m1.py tests/test_m2.py tests/test_m3.py tests/test_m4.py tests/test_m5.py tests/test_insight.py tests/test_evict.py tests/test_evals.py tests/test_bigref.py tests/test_freeze.py tests/test_witness_basis.py tests/test_d1_census.py tests/test_rehearse_writes.py; do python3 "$f" >/dev/null || echo "$f FAIL"; done
```

## 怎么继续

- 门槛的机械一半用 `scripts/d1_census.py` 判（只读快照；退出码 0/1/2，2＝判定人口为空，不能确立）。
- 实跑之前先预演：`scripts/rehearse_writes.py` 把冻结快照的 small 正文按现在的写入规则重写进临时库，再对它普查（不发模型请求）；称呼册要先用 `scripts/backfill_epithets.py` 拿历史回填。
- 判定程序与分子分母住 [`docs/SCHEMA.md`](docs/SCHEMA.md) 的「门槛判定程序」；阈值住 ROADMAP 的门槛行；不要在两处写同一个数。
- 运行、日志、读数的日常操作住 [`docs/RUNBOOK.md`](docs/RUNBOOK.md)。
- 花销：本地 CPU 是 `idle-compute`，可以直接跑；模型端点 `127.0.0.1:4000` 是 `resource`——它不在本目录手里，但**掉了自己起**：`~/.venv/bin/litellm --config ~/litellm.yml`（后台、日志进自己的 run.log），不把它当 person 的待办再发一封信。断言端点先读端口（`ss -ltnp | grep :4000`、`curl /health/liveliness`），不读进程表；端点活着也不得本机替它编一个模型。

## 东西在哪

| 要什么 | 在哪 |
|---|---|
| 引擎 | `backseat/`（`witness.py` 是证据基与写入规则唯一的地方） |
| 评测尺子 / 题集 / 快照冻结 / 普查 / 预演 / 回填 | `evals/`、`scripts/freeze_snapshot.py`、`scripts/d1_census.py`、`scripts/rehearse_writes.py`、`scripts/backfill_epithets.py` |
| 测试 | `tests/`（每个文件独立可跑，末尾打印 `ALL PASS（N 条断言）`） |
| 数据 | `~/backseat-data/backseat.db`、`frames/`、`trajectory/`、`snapshots/<UTC 日期>/` |
| 独立评审与本草稿 | `.scratch/d1-b-independent/`（评审 + 抽样 + 裁剪图，已提交） |
| 传感器能给什么（实测） | `docs/SENSORS.md` + `scripts/atspi_probe.py`（只数接口与字符数，不打印正文） |
| 关于这个人的一切 | `docs/PEOPLE.md`（唯一一份；别处只写指向它的路径） |

## 哪些文档不是当前的

- `docs/PAIN.md` 的「证据」节：数字是 2026-10-23T12:21Z 的实时库读数，随采集漂；引用前按 RUNBOOK 验证节的查询重读一次，别把旧数当当下的库。
- `docs/ROADMAP.md` 的「状态（当前事实）」表里的确认门列：Release 2.0 的确认门有一半要用户亲手做（实跑一天、callback 演示、弹幕打分、全新机器安装），别把它读成"没人管"。
- `docs/WALKTHROUGH.md` 是产品走查脚本，不是规格；规格住 `docs/DESIGN.md` 与 `docs/SCHEMA.md`。
