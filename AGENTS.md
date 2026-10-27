# backseat 入口

**这是给本节点下一次会话看的。** `backseat` 是一个本机优先的 AI 长期伴随程序（屏幕感知 → 见证层 JSON 事实
→ 记忆金字塔 → 人格层 → 弹幕出口），文档与代码都是中文注释；本目录同时是持有它 roadmap 的那个节点。
文档按 `AGENTS.md` 的层级住 `docs/<N>_<NAME>.md`，每篇内部分 `EXPLICIT` / `CONFIRMED` / `INFERRED` 三区；
一个意思只在一处有正文，别处只给指针。

## 怎么开始

1. 读 [`docs/0_ROADMAP.md`](docs/0_ROADMAP.md)：要交的里程碑、门槛、判定人口、当前状态行，以及
   **"等你一句"**（哪些事等用户拍板）。**M0″（题面→外部材料→评论）已于 2026-10-27 获用户授权，
   是当前主线；工作包 BS-M0PP-09 进行中。** Release 2.0 转维护态。
2. 读 [`docs/1_PROBLEM.md`](docs/1_PROBLEM.md)：谁疼、疼在哪、哪些痛不打算解决；关于这个人的可观察记录也在这里（唯一一份）。
3. 碰传感器/换终端/改采集之前读 [`docs/3_REQUIREMENT.md`](docs/3_REQUIREMENT.md)：那里面是"这条通道到底能拿到什么"
   的实测读数与过期条件，不是设计愿望。
4. 读 `inbox/` 与 `mail/`（若有）：每封信开头是 `kind/from/to/spend`，读完先落地到 `docs/` 再删信、再提交。
   收信与发信都走 `inbox/`：上一层的契约落在 `inbox/<日期>-*.md`，我给 root 的报告也写在那里（`to: root`）。
5. 跑一遍测试（15 个文件，约 20 秒）：

```sh
for f in tests/test_m0.py tests/test_m1.py tests/test_m2.py tests/test_m3.py tests/test_m4.py tests/test_m5.py tests/test_insight.py tests/test_evict.py tests/test_evals.py tests/test_bigref.py tests/test_freeze.py tests/test_witness_basis.py tests/test_d1_census.py tests/test_rehearse_writes.py tests/test_grounding.py; do python3 "$f" >/dev/null || echo "$f FAIL"; done
```

## 怎么继续

- 门槛的机械一半用 [`scripts/d1_census.py`](scripts/d1_census.py) 判（只读快照；退出码 0/1/2，2＝判定人口为空或分母为 0，不能确立）。
- 实跑之前先预演：[`scripts/rehearse_writes.py`](scripts/rehearse_writes.py) 把冻结快照的 small 正文按现在的写入规则重写进临时库，
  再对它普查（不发模型请求）；称呼册要先用 [`scripts/backfill_epithets.py`](scripts/backfill_epithets.py) 拿历史回填。
- 度量定义住 [`docs/2_GOAL.md`](docs/2_GOAL.md)，阈值住 ROADMAP 的门槛行，跑法与复算命令住
  [`docs/9_IMPLEMENTATION.md`](docs/9_IMPLEMENTATION.md)；不要在两处写同一个数。
- 花销：本地 CPU 是 `idle-compute`，可以直接跑；模型端点 `127.0.0.1:4000` 是 `resource`——它不在本目录手里，
  但**掉了自己起**：`~/.venv/bin/litellm --config ~/litellm.yml`（后台、日志进 run.log），不把它当 person 的待办再发一封信。
  断言端点先读端口（`ss -ltnp | grep :4000`、`curl /health/liveliness`），不读进程表；端点活着也不得本机替它编一个模型。

## 东西在哪

| 要什么 | 在哪 |
|---|---|
| 引擎 | `backseat/`（`witness.py` 是证据基与写入规则唯一的地方） |
| 评测尺子 / 题集 / 快照冻结 / 普查 / 预演 / 回填 | `evals/`、`scripts/freeze_snapshot.py`、`scripts/d1_census.py`、`scripts/rehearse_writes.py`、`scripts/backfill_epithets.py` |
| 测试 | `tests/`（每个文件独立可跑，末尾打印 `ALL PASS（N 条断言）`） |
| 数据 | `~/backseat-data/backseat.db`、`frames/`、`trajectory/`、`snapshots/<UTC 日期>/` |
| 独立评审与本草稿 | `.scratch/d1-b-independent/`（评审 + 抽样 + 裁剪图，已提交） |
| 传感器能给什么（实测） | `docs/3_REQUIREMENT.md` + `scripts/atspi_probe.py`（只数接口与字符数，不打印正文） |
| 关于这个人的一切 | `docs/1_PROBLEM.md`（唯一一份；别处只写指向它的路径） |

## 哪些文档不是当前的

- `docs/1_PROBLEM.md` 的证据节：数字是 2026-10-23T12:21Z 的实时库读数，随采集漂；引用前按
  `docs/9_IMPLEMENTATION.md` 验证节的查询重读一次，别把旧数当当下的库。
- `docs/0_ROADMAP.md` 的确认门列：Release 2.0 的确认门有一半要用户亲手做（实跑一天、callback 演示、弹幕打分、
  全新机器安装），别把它读成"没人管"；"等你一句"里的每一条都还没答。
- `docs/5_PRODUCT.md` 的走查剧本是走查脚本（示意 id、时刻与期望输出），不是规格；规格住同文件前半的交互节与
  `docs/7_INTERFACE.md`。
