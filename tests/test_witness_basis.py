"""见证层证据基冒烟（批窗口 / 切换句基帧 / 通行称呼）：一行只写它自己 obs_ids 撑得住的话。

SCHEMA §语义层证据引用 的三条写入规则各一节；它们不是门槛，门槛住 ROADMAP，由项目自己的节点判。

覆盖：
a) 批窗口取被引用观察的 min/max：`id` 与 `ts_wall` 不同序时不再产生倒置窗口、不再有
   落在自窗外的 obs_ids 成员（旧写法 ts_start=rows[0].ts_wall、ts_end=rows[-1].ts_wall
   在这种输入上窗口倒置且成员出窗——本测试把旧写法的结果也断言出来，判别力不靠注释）；
b) `从X切至Y` 切换句把被切离端并进本行 obs_ids：本轮帧 + 基帧，窗口覆盖过渡两端；
   模型点名 since 时以点名为准（可指到上上条），没有切换句的行不连带基帧；
c) 通行称呼只认本轮点名的图：**沿用的**称呼没点名 → 整行拒收（正文一个字节不改写、
   不落库、stats 计数可见），点名图号越界同样拒收，点名有效则保留且记 `payload.cast`；
   首次出现的称呼放行，被沿用后同样整行拒收；无帧可看时任何称呼都没有依据（不是首次
   出现的例外）；
d) 对比/过渡词面的闭集：表里每一族都要求基帧（`切换为`/`相较上一帧`/`仍为` 三行各得
   一个基帧、窗口覆盖两端），表外的行不连带；旧判据（只认 `从…切至`）跑同一夹具时
   这三行拿不到基帧——判别力不靠注释；
e) 称呼册在库里（`epithets` 表）：隔一行再写的称呼仍整行拒收（旧册只记上一行，中间那行
   一提不到它就漏），被拒的词面留在册里所以再写还是拒，点名才留；归一化用在匹配上
   （“穿X的Y” ≡ “XY”）；册子不封顶（预填 250 个词面后最早那个再写仍拒）；关掉 Store
   再开册子还在；整行拒收后库里不留半个名字、也不吃掉标记词；
f) 保留的点名进 `payload.cast`：点名的行有，被拒的行整行不在；普查 C2 据此可判；
g) 连续对比段不累积：一连串对比句每行只拿上一行**最近的那一帧**作基帧，obs_ids 长度恒为
   本轮帧数 +1、窗口只往前一个台阶；旧写法（整行 `obs_ids` 并入）在同夹具上逐行长大。
   运行：python3 tests/test_witness_basis.py [batch|switch|epithet|markers|cascade|memory|norm|cap|reject|noframes|persist|cast]
      （不带参数跑全部；分节是为了能在旧代码上单独跑出新节的红）
"""

import json
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from backseat.clock import fmt_wall
from backseat.config import Config
from backseat.store import Store
from backseat.trajectory import Trajectory
from backseat.witness import Witness  # 旧代码也有 Witness：批窗口一节因此在旧代码上可跑

T = 1_700_000_000.0  # 固定墙钟基准：测试不依赖跑起来的时刻
# 改动前的标记判据原文（只认 `从…切至`）：断言旧行为时用它，不用记忆里的写法。
OLD_SWITCH_RE = re.compile(r"从[^，。；,;\s]{1,24}?切至")
CHECKS = 0


def check(cond, msg) -> None:
    """计数断言：最后打印总数，报告里能说清这次到底查了多少条。"""
    global CHECKS
    CHECKS += 1
    assert cond, msg


class FakeModel:
    model = "fake-1"
    kind = "fake"

    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def chat(self, messages):
        self.calls.append(messages)
        return self.outputs.pop(0), {"in": 100, "out": 20, "cached": 50, "latency_ms": 12}


def anchor(text="桌面在终端", intent="写代码", **kw):
    return json.dumps({"type": "anchor", "describe": text, "intent": intent, **kw},
                      ensure_ascii=False)


def diff(text="改了 store.py", intent="继续写", **kw):
    return json.dumps({"type": "diff", "diff": text, "intent": intent, **kw},
                      ensure_ascii=False)


def jpeg(path: Path, size: int = 60000) -> str:
    """占位帧：过得了 <40KB 黑帧预过滤，也能被 base64 读进来。"""
    path.write_bytes(b"\xff\xd8" + b"0" * (size - 2))
    return str(path)


def mk_obs(store: Store, wall: float, ref: str | None = None,
           kind: str = "screen.notable", nbytes: int = 60000) -> int:
    return store.insert("observations", ts_wall=wall, ts_mono=wall - T,
                        sensor="screen", kind=kind, display="primary",
                        key=f"k{wall}", ref=ref, bytes=nbytes if ref else 0)


def obs_row(store: Store, oid: int):
    return store.conn.execute("SELECT * FROM observations WHERE id=?", (oid,)).fetchone()


def last_small(store: Store):
    return store.conn.execute(
        "SELECT * FROM messages WHERE level='small' ORDER BY id DESC LIMIT 1").fetchone()


def wall_of(store: Store, oid: int) -> float:
    return store.conn.execute(
        "SELECT ts_wall FROM observations WHERE id=?", (oid,)).fetchone()["ts_wall"]


def payload_of(row) -> dict:
    return json.loads(row["payload"] or "{}")


def row_of(store: Store, mid: int):
    return store.conn.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone()


def small_count(store: Store) -> int:
    return store.conn.execute(
        "SELECT count(*) AS c FROM messages WHERE level='small'").fetchone()["c"]


def book(store: Store) -> set[str]:
    """称呼册（库表）里的全部词面。"""
    return {r["form"] for r in store.conn.execute("SELECT form FROM epithets")}


def make_witness(store: Store, td: str, outputs) -> tuple[Witness, FakeModel]:
    """锚已就位、批次阈值很大的见证者：默认走 diff 路径，模型输出由 outputs 给定。"""
    store.set_state("anchor", {"ts_wall": T, "n": 1})
    model = FakeModel(outputs)
    cfg = Config(anchor_every_n=100, anchor_age_min=600)
    return Witness(store, model, Trajectory(td), cfg, log=lambda *a: None), model


# —— a) 批窗口：id 序与 ts_wall 序不同 ——
def section_batch_window() -> None:
    with tempfile.TemporaryDirectory() as td:
        store = Store(Path(td) / "batch.db")
        frame = jpeg(Path(td) / "frame.jpg")
        # 真实库里抓帧多路并发，插库顺序（id）与墙钟不同序：这里故意让 id 序的
        # 首尾都不是极值——旧写法必然吃到倒置窗口。
        walls = [T + 6, T + 2, T + 8, T + 4, T + 10, T + 0]
        ids = [mk_obs(store, w, frame) for w in walls]
        w, _model = make_witness(store, td, [diff("在终端里改 store.py")])

        calls = w.process_pending(batch_size=6)
        check(calls == 1, f"6 帧应合成 1 次批调用（实际 {calls}）")
        row = last_small(store)
        cited = json.loads(row["obs_ids"])
        check(sorted(cited) == sorted(ids), f"批行的 obs_ids 应含全部 6 帧：{cited} vs {ids}")
        check(row["ts_start"] <= row["ts_end"],
              f"窗口不得倒置：ts_start={row['ts_start']} ts_end={row['ts_end']}")
        check((row["ts_start"], row["ts_end"]) == (min(walls), max(walls)),
              f"窗口应取被引用观察的 min/max：{(row['ts_start'], row['ts_end'])} "
              f"vs {(min(walls), max(walls))}")
        outside = [i for i in cited if not row["ts_start"] <= wall_of(store, i) <= row["ts_end"]]
        check(not outside, f"obs_ids 成员不得落在自窗外：{outside}")
        # 旧写法的结果，写进断言：本测试在旧代码上必须红，否则它证明不了什么
        old_start, old_end = walls[0], walls[-1]
        old_outside = [i for i in ids if not old_start <= wall_of(store, i) <= old_end]
        check(old_start > old_end,
              f"夹具应让旧写法倒置：rows[0]={old_start} rows[-1]={old_end}")
        check(len(old_outside) >= 2,
              f"夹具应让旧写法把成员留在窗外：{[(i, wall_of(store, i)) for i in old_outside]}")
        print(f"batch window OK: 窗口 {fmt_wall(row['ts_start'])}~{fmt_wall(row['ts_end'])} "
              f"含 6 帧；旧写法会得倒置窗口 + {len(old_outside)} 个出窗成员")
        store.close()


# —— b) 切换句：两端同在一行 obs_ids ——
def section_switch_base() -> None:
    from backseat.witness import (
        parse_stamp,  # 新增：旧代码没有它，故本节只在修复后的树上跑
    )
    with tempfile.TemporaryDirectory() as td:
        store = Store(Path(td) / "switch.db")
        frame = jpeg(Path(td) / "frame.jpg")
        a = mk_obs(store, T, frame)
        b = mk_obs(store, T + 10, frame)
        c = mk_obs(store, T + 20, frame)
        d = mk_obs(store, T + 30, frame)
        w, _model = make_witness(store, td, [
            anchor("终端在跑测试脚本"),
            diff("从终端切至浏览器，MDN 的 flex 章节"),
            diff("从浏览器切至终端", since=fmt_wall(T)),  # 点的是上上条（obs a）
            diff("继续在读终端里的报错"),
        ])
        ra = w.process(obs_row(store, a))
        rb = w.process(obs_row(store, b))
        rc = w.process(obs_row(store, c))
        rd = w.process(obs_row(store, d))
        check(all(x is not None for x in (ra, rb, rc, rd)), "四行都应落库")

        row_b = store.conn.execute("SELECT * FROM messages WHERE id=?", (rb,)).fetchone()
        check(json.loads(row_b["obs_ids"]) == [b, a],
              f"切换行的 obs_ids 应是本轮帧 + 基帧：{row_b['obs_ids']}")
        check(payload_of(row_b).get("switch_base") == [a],
              f"payload 应记明基帧：{payload_of(row_b)}")
        check((row_b["ts_start"], row_b["ts_end"]) == (T, T + 10),
              f"窗口应覆盖过渡两端：{(row_b['ts_start'], row_b['ts_end'])}")

        row_c = store.conn.execute("SELECT * FROM messages WHERE id=?", (rc,)).fetchone()
        check(json.loads(row_c["obs_ids"]) == [c, a],
              f"模型点名 since 时应以点名为基帧（不是上一条 b）：{row_c['obs_ids']}")
        check(b not in json.loads(row_c["obs_ids"]),
              "点名了基帧就不该再塞上一条：端点是模型说出来的那一端")

        row_d = store.conn.execute("SELECT * FROM messages WHERE id=?", (rd,)).fetchone()
        check(json.loads(row_d["obs_ids"]) == [d],
              f"没有切换句的行不该连带基帧：{row_d['obs_ids']}")
        check("switch_base" not in payload_of(row_d), f"无切换句就不该记基帧：{payload_of(row_d)}")
        print(f"switch basis OK: 从X切至Y 行 obs_ids={json.loads(row_b['obs_ids'])} "
              f"since 点名行 obs_ids={json.loads(row_c['obs_ids'])} 非切换行={json.loads(row_d['obs_ids'])}")

        # parse_stamp 只认我们自己印出来的时间格式；认不出就是 None（不默认成 now）
        check(parse_stamp(fmt_wall(T), T + 5) is not None, "fmt_wall 时间应能解析回墙钟")
        check(parse_stamp("不知道", T + 5) is None, "解析不出就返回 None")
        store.close()


# —— c) 通行称呼：只认本轮点名的图；沿用而未点名 → 整行拒收 ——
def section_epithet() -> None:
    """沿用的称呼没点名本轮某张图 → 整行拒收（正文一个字节不改写、不落库），首次出现放行。"""
    from backseat.witness import epithet_forms
    with tempfile.TemporaryDirectory() as td:
        store = Store(Path(td) / "epithet.db")
        frame = jpeg(Path(td) / "frame.jpg")
        o1, o2, o3 = (mk_obs(store, T + i * 10, frame) for i in range(3))
        o4a, o4b = mk_obs(store, T + 40, frame), mk_obs(store, T + 50, frame)
        o5, o6 = mk_obs(store, T + 60, frame), mk_obs(store, T + 70, frame)
        o7, o8 = mk_obs(store, T + 80, frame), mk_obs(store, T + 90, frame)
        o9 = mk_obs(store, T + 100, None)  # 帧已被驱逐：ref 为 NULL，本轮无图可看
        name = "浅色外套男子"
        w, _model = make_witness(store, td, [
            # 1) 首行：新称呼 + 点名本轮第 1 张图 → 保留
            anchor(f"{name}在蓝砖拱廊下跳街舞", intent="跳舞",
                   cast=[{"name": name, "frame": 1}]),
            # 2) 上一行用过的称呼，本轮没点名 → 整行拒收
            diff(f"{name}仍在原地", intent="跳舞"),
            # 3) 点名本轮第 1 张图 → 保留
            diff(f"{name}在蓝砖拱廊下跳街舞", intent="跳舞",
                 cast=[{"name": name, "frame": 1}]),
        ])
        n0 = small_count(store)
        r1 = w.process(obs_row(store, o1))
        check(r1 is not None, "点名有效：首行的称呼只可能来自本帧，应落库")
        row1 = row_of(store, r1)
        check(name in row1["content"], f"点名了本轮图的称呼应保留：{row1['content']!r}")
        check(name in book(store), "用过的称呼应进册，供下一行核对")

        refused0 = w.stats.get("epithet_refused", 0)
        r2 = w.process(obs_row(store, o2))
        check(r2 is None, f"沿用的称呼没点名 → 整行拒收（不落库）：r={r2}")
        check(small_count(store) == n0 + 1, f"拒收的行不该落库：{n0}->{small_count(store)}")
        check(w.stats.get("epithet_refused", 0) > refused0, f"拒收要计数：{w.stats}")
        check(w.stats.get("discarded", 0) >= 1, f"拒收的行记进 discarded：{w.stats}")
        check(name in book(store), "被拒的词面仍在册里，下次不点名还会被拒")

        r3 = w.process(obs_row(store, o3))
        check(r3 is not None, "点名有效就留下")
        row3 = row_of(store, r3)
        check(name in row3["content"], f"点名有效的称呼应保留：{row3['content']!r}")
        check(not payload_of(row3).get("epithet_refused"),
              f"保留时不该记拒绝：{payload_of(row3)}")
        check(payload_of(row3).get("cast") == [{"name": name, "frame": 1}],
              f"点名有效的行应记明点的是哪一帧：{payload_of(row3)}")

        # 4) 批行：点名图号越界（本轮只有 2 张图，点名第 3 张）→ 整行拒收
        w.model.outputs.append(diff(f"{name}站着", cast=[{"name": name, "frame": 3}]))
        n_before = small_count(store)
        r4 = w.process_batch([obs_row(store, o4a), obs_row(store, o4b)])
        check(r4 is None and small_count(store) == n_before,
              f"越界图号等于没点名：整行拒收（r={r4}）")
        check(w.stats.get("discarded", 0) >= 3, f"批拒收丢的是整批：{w.stats}")

        # 5) 重新点名有效 → 保留
        w.model.outputs.append(diff(f"{name}看着屏幕", cast=[{"name": name, "frame": 1}]))
        r5 = w.process(obs_row(store, o5))
        check(r5 is not None and name in row_of(store, r5)["content"],
              "重新点名有效的称呼应保留")
        check(name in book(store), "保留下来的称呼仍在册里")

        # 6) 正文只剩称呼本身、又没点名 → 整行拒收（落一行空话比不落更糟）
        w.model.outputs.append(diff(name))
        before = small_count(store)
        r6 = w.process(obs_row(store, o6))
        check(r6 is None and small_count(store) == before,
              f"整行只剩沿用的称呼：拒收、不落库：r={r6} {before}->{small_count(store)}")

        # 7) 新称呼（册里没有）不该被误删；8) 它被沿用后同样整行拒收
        w.model.outputs.append(diff("红发男子在麦克风前"))
        r7 = w.process(obs_row(store, o7))
        check(r7 is not None, "首次出现的称呼照旧放行")
        row7 = row_of(store, r7)
        check(row7["content"] == "红发男子在麦克风前", f"首次出现的称呼不该被动：{row7['content']!r}")
        check(any("红发男子" in f for f in book(store)), f"新称呼应入册：{sorted(book(store))[-3:]}")
        w.model.outputs.append(diff("红发男子仍在麦克风前"))
        r8 = w.process(obs_row(store, o8))
        check(r8 is None, f"沿用而来的新称呼同样要拒：{r8}")

        # 9) 无帧可看（ref 被驱逐）→ 任何人物称呼都没有依据，点名也不算
        w.model.outputs.append(diff(f"{name}在走动", cast=[{"name": name, "frame": 1}]))
        r9 = w.process(obs_row(store, o9))
        check(r9 is None, f"没有帧的行不许有站立描述：{r9}")
        print(f"epithet OK: 沿用即整行拒收（stats.epithet_refused={w.stats.get('epithet_refused')}），"
              f"首次出现放行、越界/无帧拒收")

        # 机械登记：后缀变体保证“穿浅色外套的男子”与“浅色外套男子”是同一个称呼
        check({"浅色外套男子", "外套男子"} <= set(epithet_forms("穿浅色外套的男子在跳舞")),
              f"应登记后缀变体：{epithet_forms('穿浅色外套的男子在跳舞')}")
        check(epithet_forms("终端里只有报错") == [], "没有人物称呼就没有候选")
        store.close()


# —— d) 对比/过渡词面的闭集：表里的族要基帧，表外不要 ——
# 评审在 50 行里数到的族，逐条词面一行；`从…切至` 是旧判据已经认得的那个，其余都是新收的。
MARKER_TEXTS = (
    ("从终端切至浏览器", "从…切至"),
    ("由终端切至浏览器", "由…切至"),
    ("由终端切为浏览器", "由…切为"),
    ("由终端切换为浏览器", "由…切换为"),
    ("从终端切为浏览器", "从…切为"),
    ("从终端切换为浏览器", "从…切换为"),
    ("画面切至浏览器", "画面切至"),
    ("镜头切至浏览器", "镜头切至"),
    ("切至浏览器", "切至"),
    ("切换为浏览器", "切换为"),
    ("相较上一帧，页面变亮", "相较上一帧"),
    ("相较前帧，页面变亮", "相较前帧"),
    ("相较早先的画面，页面变亮", "相较"),
    ("仍为同一个终端", "仍为"),
    ("仍是同一个终端", "仍是"),
    ("仍保持同一布局", "仍保持"),
    ("与此前不同", "与此前…不同"),
    ("与此前的布局不同", "与此前…不同"),
    ("相对上一张图更亮", "相对上一…"),
)


def section_markers() -> None:
    """`切换为`/`相较上一帧`/`仍为` 的三行各得一个基帧，无对比句的行不连带。"""
    from backseat.witness import SWITCH_RE

    # 闭集：表里的族逐条命中，表外一行不命中（放宽不等于“什么都算对比句”）
    missed = [fam for text, fam in MARKER_TEXTS if not SWITCH_RE.search(text)]
    check(not missed, f"标记表应认全部计数到的族，漏了：{missed}")
    for text in ("把窗口切到右边", "终端里多了一行警告", "终端在跑测试脚本"):
        check(SWITCH_RE.search(text) is None, f"表外的写法不要求基帧：{text!r}")
    # 旧判据在同一张表上的命中：只有 `从…切至` 这一族，其余全漏
    old_hits = [fam for text, fam in MARKER_TEXTS if OLD_SWITCH_RE.search(text)]
    check(old_hits == ["从…切至"], f"旧判据只认 从…切至：{old_hits}")

    with tempfile.TemporaryDirectory() as td:
        store = Store(Path(td) / "markers.db")
        frame = jpeg(Path(td) / "frame.jpg")
        ids = [mk_obs(store, T + i * 10, frame) for i in range(5)]
        marked = [("切换为浏览器，MDN 的 flex 章节", "切换为"),
                  ("相较上一帧，页面滚到报错那一行", "相较上一帧"),
                  ("仍为同一个编辑器窗口", "仍为")]
        w, _model = make_witness(store, td, [
            anchor("终端在跑测试脚本"),
            *(diff(text) for text, _ in marked),
            diff("终端里多了一行警告"),
        ])
        got = [w.process(obs_row(store, oid)) for oid in ids]
        check(all(x is not None for x in got), f"五行都应落库：{got}")

        prev_latest = ids[0]  # 上一条 small 行里 ts_wall 最新的那一帧（本夹具里就是它的本轮帧）
        for i, (text, fam) in enumerate(marked, start=1):
            row = store.conn.execute("SELECT * FROM messages WHERE id=?", (got[i],)).fetchone()
            cited = json.loads(row["obs_ids"])
            own = ids[i]
            check(cited == [own, prev_latest],
                  f"{fam} 行应只并进上一行最近的那一帧：{cited} vs 本帧 {own}、上一行最近 {prev_latest}")
            check(payload_of(row).get("switch_base") == [prev_latest],
                  f"{fam} 行应记明基帧：{payload_of(row)} vs {[prev_latest]}")
            check(row["ts_start"] == min(wall_of(store, x) for x in cited),
                  f"{fam} 行的窗口应覆盖基帧：{row['ts_start']} vs {cited}")
            # 旧行为，同夹具：旧判据在这句上返回空基帧，所以旧代码写下的只能是本轮那一帧
            check(OLD_SWITCH_RE.search(text) is None,
                  f"{fam} 不是旧判据的族（夹具应让旧代码在这行丢基帧）：{text!r}")
            check(cited != [own], f"{fam}：旧代码的 obs_ids 就是 {[own]}")
            prev_latest = own

        # 旧行为（整行 obs_ids 并入）在同一批行上的长度：2、3、4——逐行长大，收窄后恒为 2
        grown, old_prev = [], [ids[0]]
        for i in range(1, len(marked) + 1):
            old_prev = [ids[i], *old_prev]
            grown.append(len(old_prev))
        check(grown == [2, 3, 4], f"夹具应让旧写法逐行累积：{grown}")

        plain = store.conn.execute("SELECT * FROM messages WHERE id=?", (got[4],)).fetchone()
        check(json.loads(plain["obs_ids"]) == [ids[4]],
              f"表外的行不该连带基帧：{json.loads(plain['obs_ids'])}")
        check("switch_base" not in payload_of(plain), f"无对比句就不该记基帧：{payload_of(plain)}")
        print("markers OK: 切换为/相较上一帧/仍为 各得基帧，表外不连带；"
              f"旧判据在 {len(MARKER_TEXTS)} 条词面里只认 1 条")
        store.close()


# —— g) 连续对比段不累积 ——
N_CASCADE = 12


def section_no_accumulation() -> None:
    """一连串对比句：每行只拿上一行最近的那一帧，窗口只往前一个台阶。"""
    with tempfile.TemporaryDirectory() as td:
        store = Store(Path(td) / "cascade.db")
        frame = jpeg(Path(td) / "frame.jpg")
        ids = [mk_obs(store, T + i * 10, frame) for i in range(N_CASCADE)]
        w, _model = make_witness(store, td, [
            anchor("终端在跑测试脚本"),
            *(diff("从终端切至浏览器，页面在加载") for _ in range(N_CASCADE - 1)),
        ])
        got = [w.process(obs_row(store, oid)) for oid in ids]
        check(all(x is not None for x in got), f"行都应落库：{got}")

        sizes, spans = [], []
        for i, mid in enumerate(got):
            row = store.conn.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone()
            cited = json.loads(row["obs_ids"])
            sizes.append(len(cited))
            spans.append(round(row["ts_end"] - row["ts_start"], 3))
            if i == 0:  # 库里没有上一条 small 行：没有前帧可指，就不假装有
                check(cited == [ids[0]], f"首行没有前帧：{cited}")
                check("switch_base" not in payload_of(row), f"首行不该长 switch_base：{payload_of(row)}")
                continue
            check(cited == [ids[i], ids[i - 1]],
                  f"第 {i} 行应只并进上一行最近的那一帧：{cited}")
            check(payload_of(row).get("switch_base") == [ids[i - 1]],
                  f"第 {i} 行基帧应是一帧：{payload_of(row)}")

        check(max(sizes[1:]) == 2, f"连续对比段每行最多两帧（本轮 + 基帧）：{sizes}")
        check(set(spans[1:]) == {10.0}, f"窗口只往前一个台阶，不随段长长大：{spans}")

        # 旧行为（整行 obs_ids 并入）：同一条链上的长度逐行长大，末行把整段都带上
        grown, old_prev = [], [ids[0]]
        for i in range(1, N_CASCADE):
            old_prev = [ids[i], *old_prev]
            grown.append(len(old_prev))
        check(grown[-1] == N_CASCADE, f"夹具应让旧写法累积到段长：{grown[-1]}")
        check(grown[1] > grown[0] and grown[-1] > max(sizes),
              f"旧写法逐行长大而收窄后恒定：旧 {grown} / 新 {sizes}")

        # 中间一首非对比行：下一行对比句的基帧仍是它的本轮帧，不是整段
        plain = mk_obs(store, T + N_CASCADE * 10, frame)
        _model.outputs.append(diff("终端里多了一行警告"))
        mid_plain = w.process(obs_row(store, plain))
        after = mk_obs(store, T + (N_CASCADE + 1) * 10, frame)
        _model.outputs.append(diff("从终端切至浏览器，页面在加载"))
        mid_after = w.process(obs_row(store, after))
        row_after = store.conn.execute("SELECT * FROM messages WHERE id=?", (mid_after,)).fetchone()
        check(json.loads(row_after["obs_ids"]) == [after, plain],
              f"隔一首普通行后仍只并一帧：{json.loads(row_after['obs_ids'])}")
        check(mid_plain is not None, "中间那首普通行应落库")

        print(f"no accumulation OK: {N_CASCADE} 行连续对比段每行 obs_ids={sizes}（旧写法会长到 {grown[-1]}），"
              f"窗口恒 10s")
        store.close()


# —— e) 称呼册在库里：隔着行也拦得住，跨重启还在 ——
def section_epithet_memory() -> None:
    """隔一行再写的称呼仍整行拒收；中间那行不提它，册子不因此清空。"""
    from backseat.witness import epithet_forms
    with tempfile.TemporaryDirectory() as td:
        store = Store(Path(td) / "memory.db")
        frame = jpeg(Path(td) / "frame.jpg")
        o = [mk_obs(store, T + i * 10, frame) for i in range(6)]
        name = "浅色外套男子"
        w, _model = make_witness(store, td, [
            anchor(f"{name}在蓝砖拱廊下跳街舞", cast=[{"name": name, "frame": 1}]),  # 入册
            diff("终端里只剩一行报错"),               # 中间一行不提它
            diff(f"{name}仍在原地"),                    # 隔一行沿用、没点名 → 整行拒收
            diff(f"{name}还在原地"),                    # 再写一次 → 还拒
            diff(f"{name}向前一步", cast=[{"name": name, "frame": 1}]),  # 点名 → 留
            diff("花衣男子站在柜台前"),                 # 新称呼：不误删
        ])
        mids = [w.process(obs_row(store, oid)) for oid in o]
        rows = {i: row_of(store, mid) for i, mid in enumerate(mids) if mid is not None}

        check(0 in rows and name in rows[0]["content"], "首行点名有效的称呼应留下")
        check(name in book(store), f"首次出现的称呼应入册：{sorted(book(store))}")
        # 旧行为，同夹具：中间这一行没有任何称呼，所以“只记上一行”的册子在这里是空的
        check(epithet_forms(rows[1]["content"]) == [],
              f"夹具应让中间行的称呼集为空：{rows[1]['content']!r}")
        check(name in book(store),
              "累计册：中间行不提它也不该把它忘掉（旧册在这里是空）")

        check(mids[2] is None and mids[3] is None,
              f"隔一行沿用、没点名 → 整行拒收（{mids[2]}、{mids[3]}）")
        check(w.stats.get("epithet_refused", 0) >= 2, f"两次拒收都要计数：{w.stats}")
        check(name in book(store), "点名之前册子不该把它丢掉（它还是上一次看到的那个称呼）")
        check(4 in rows and name in rows[4]["content"], f"点名有效就留下：{rows.get(4)}")
        check(payload_of(rows[4]).get("cast") == [{"name": name, "frame": 1}],
              f"点名有效的行应记明点的是哪一帧：{payload_of(rows[4])}")

        check(5 in rows and "花衣男子" in rows[5]["content"],
              f"新称呼不该被误删：{rows.get(5)}")
        check("花衣男子" in book(store), "新称呼应入册")
        check("cast" not in payload_of(rows[5]),
              f"没点名的行不该长 cast 键：{payload_of(rows[5])}")
        print("epithet memory OK: 隔一行沿用即整行拒收，再写仍拒，点名才留，新称呼不误删")
        store.close()


# —— e2) 归一化用在匹配上：模型写“穿X的Y”、册里是“XY” ——
def section_epithet_normalized() -> None:
    """同一称呼隔行再写；模型写“穿米色大衣的男子”而册里是“米色大衣男子” → 仍被拒。"""
    from backseat.witness import epithet_forms
    with tempfile.TemporaryDirectory() as td:
        store = Store(Path(td) / "norm.db")
        frame = jpeg(Path(td) / "frame.jpg")
        o = [mk_obs(store, T + i * 10, frame) for i in range(3)]
        name = "米色大衣男子"           # 册里记的是去“的”后的词面
        written = "右侧穿米色大衣的男子在走动"  # 模型原文里有“的”，字面不含 name
        w, _model = make_witness(store, td, [
            diff(written),
            diff("终端里只剩一行报错"),
            diff(written),
        ])
        r0 = w.process(obs_row(store, o[0]))
        check(r0 is not None, "首次出现应放行")
        check(name in epithet_forms(written) and name not in written,
              f"夹具应让字面比对失手：{name!r} in {written!r}?")
        check(name in book(store), f"归一化后的词面应入册：{sorted(book(store))}")
        check(w.process(obs_row(store, o[1])) is not None, "中间行应落下")
        r2 = w.process(obs_row(store, o[2]))
        check(r2 is None, f"字面不同、归一化相同 → 仍整行拒收：{r2}")
        check(w.stats.get("epithet_refused", 0) >= 1, f"拒收计数：{w.stats}")
        print(f"epithet normalized OK: “{written}” 归一化后命中册里的 {name}，整行拒收")
        store.close()


# —— e3) 册子不封顶：预填 >200 个词面后，最早那个再写仍整行拒收 ——
def section_epithet_cap() -> None:
    """旧设计封顶 200，被忘掉的词面再写就是静默沿用；现在不封顶。"""
    from backseat.witness import EPITHET_TABLE

    with tempfile.TemporaryDirectory() as td:
        store = Store(Path(td) / "cap.db")
        frame = jpeg(Path(td) / "frame.jpg")
        w, model = make_witness(store, td, [diff("黄衣男子站在门口")])
        old = [chr(0x4E00 + i) + chr(0x5200 + i) + "男子" for i in range(250)]
        store.conn.executemany(
            f"INSERT INTO {EPITHET_TABLE}(form,first_wall,last_wall) VALUES(?,?,?)",
            [(f, T, T) for f in old])
        store.conn.commit()
        check(len(book(store)) == 250, f"预填应进册：{len(book(store))}")

        oid = mk_obs(store, T, frame)
        model.outputs.insert(0, diff(f"{old[0]}站在门口"))
        refused0 = w.stats.get("epithet_refused", 0)
        mid = w.process(obs_row(store, oid))
        check(mid is None, f"预填 250 个后最早的词面再写仍应整行拒收：{mid}")
        check(w.stats.get("epithet_refused", 0) > refused0, f"拒收计数：{w.stats}")
        check(old[0] in book(store) and len(book(store)) >= 250,
              f"册子不封顶，最早的词面还在：{old[0] in book(store)} / {len(book(store))}")

        oid2 = mk_obs(store, T + 10, frame)
        mid2 = w.process(obs_row(store, oid2))
        check(mid2 is not None and "黄衣男子" in row_of(store, mid2)["content"],
              "册子大不该误伤首次出现的词面")
        print(f"epithet cap OK: 预填 250 个词面后最早的 {old[0]} 仍被拒，册里现有 {len(book(store))}")
        store.close()


# —— e4) 整行拒收：正文不改写，不留半个名字、不吃掉标记词 ——
def section_epithet_reject() -> None:
    """沿用的词面没点名 → 整行不落库：既不剩半截名字，也不把标记词吃进删除里。"""
    with tempfile.TemporaryDirectory() as td:
        store = Store(Path(td) / "reject.db")
        frame = jpeg(Path(td) / "frame.jpg")
        o1, o2 = mk_obs(store, T, frame), mk_obs(store, T + 10, frame)
        name = "浅色外套男子"
        bad = f"画面切换为{name}的近景"   # 标记词“切换为”紧贴着要拒的名字
        w, _model = make_witness(store, td, [diff(name + "在拱廊下"), diff(bad)])
        r1 = w.process(obs_row(store, o1))
        check(r1 is not None, "首次出现应放行")
        check(w.process(obs_row(store, o2)) is None, "沿用未点名 → 整行拒收")
        rows = [r["content"] for r in store.conn.execute(
            "SELECT content FROM messages WHERE level='small' ORDER BY id")]
        check(len(rows) == 1, f"拒收的行不该落库（只有首行）：{rows}")
        check(not any("切换为" in c or "的近景" in c for c in rows),
              f"拒收的行整行不存在，正文没被改写：{rows}")
        check(not any("男子" in c and name not in c for c in rows),
              f"库里不留半个名字：{rows}")
        print("epithet reject OK: 整行拒收，库里无半截名字、标记词未被吃掉")
        store.close()


# —— e5) 无帧：任何词面都没有依据（首次出现也拒） ——
def section_epithet_noframes() -> None:
    """本轮一张图都没有（ref 为 NULL / 被驱逐）：正文有词面就整行拒收，首次出现也一样。"""
    with tempfile.TemporaryDirectory() as td:
        store = Store(Path(td) / "noframes.db")
        oid = mk_obs(store, T, None)  # 无 ref：本轮图数 0
        w, _model = make_witness(store, td, [diff("浅色外套男子站在门口")])
        before = small_count(store)
        refused0 = w.stats.get("epithet_refused", 0)
        mid = w.process(obs_row(store, oid))
        check(mid is None, f"无帧 + 人物称呼 → 整行拒收：{mid}")
        check(small_count(store) == before, "无帧拒收的行不落库")
        check(w.stats.get("epithet_refused", 0) > refused0, f"拒收计数：{w.stats}")
        print("epithet noframes OK: 本轮无帧，正文有词面即整行拒收（不是首次出现的例外）")
        store.close()


# —— e6) 册子在库里：关掉 Store 再开，册子还在 ——
def section_epithet_persist() -> None:
    """册子落在库表里，不跟进程走：关掉再开，词面仍在，沿用仍被拒。"""
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "persist.db"
        frame = jpeg(Path(td) / "frame.jpg")
        name = "浅色外套男子"
        store = Store(path)
        o1 = mk_obs(store, T, frame)
        w, _model = make_witness(store, td, [diff(name + "在拱廊下")])
        check(w.process(obs_row(store, o1)) is not None, "首次出现应放行")
        store.close()

        store2 = Store(path)  # 新连接 = 新进程的等价物
        check(name in book(store2), f"关掉再开册子应还在：{sorted(book(store2))}")
        o2 = mk_obs(store2, T + 10, frame)
        w2, _model2 = make_witness(store2, td, [diff(name + "仍在拱廊下")])
        check(w2.process(obs_row(store2, o2)) is None, "新连接里同一个沿用仍被拒")
        print(f"epithet persist OK: 重开库后册里还有 {len(book(store2))} 个词面，沿用仍拒收")
        store2.close()


# —— f) 保留的点名进 payload.cast：C2 事后可判 ——
def section_cast_record() -> None:
    """留下来的点名记进 `payload.cast`；被拒的行整行不落库、连键都不长。"""
    with tempfile.TemporaryDirectory() as td:
        store = Store(Path(td) / "cast.db")
        frame = jpeg(Path(td) / "frame.jpg")
        o = [mk_obs(store, T + i * 10, frame) for i in range(5)]
        name = "浅色外套男子"
        w, _model = make_witness(store, td, [
            anchor(f"{name}在蓝砖拱廊下跳街舞", cast=[{"name": name, "frame": 1}]),  # 点名有效
            diff(f"{name}仍在原地"),                                   # 沿用、没点名 → 整行拒收
            diff(f"{name}向前一步", cast=[{"name": name, "frame": 1}]),  # 点名有效
            diff("画面里多了一张空椅子", cast=[{"name": name, "frame": 1}]),  # 点了名但没写进正文
            diff(f"{name}又出现在右侧", cast=[{"name": name, "frame": 3}]),  # 图号越界 → 拒收
        ])
        mids = [w.process(obs_row(store, oid)) for oid in o]
        rows = {i: row_of(store, mid) for i, mid in enumerate(mids) if mid is not None}

        check(payload_of(rows[0]).get("cast") == [{"name": name, "frame": 1}],
              f"保留的点名应原样记下：{payload_of(rows[0])}")
        check(mids[1] is None and mids[4] is None,
              f"没点名的沿用 / 图号越界 → 整行拒收（{mids[1]}、{mids[4]}）")
        check(payload_of(rows[2]).get("cast") == [{"name": name, "frame": 1}],
              f"点名有效的行应记明点的是哪一帧：{payload_of(rows[2])}")
        check("cast" not in payload_of(rows[3]),
              f"点了名但名字没留下 → 没有保留的点名，不长 cast 键：{payload_of(rows[3])}")

        # 尺子读得到它：C2 的分母是“被沿用的称呼”，分子是带 cast 的那些（0 静默沿用）
        sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
        import d1_census
        small = store.conn.execute(
            "SELECT id, ts_start, ts_end, content, payload, obs_ids FROM messages "
            "WHERE level='small' ORDER BY id").fetchall()
        c = d1_census.census_c(small)
        check(c["c2_carried"] == 1 and c["c2_cast"] == 1,
              f"沿用行应恰好一行且带 cast：carried={c['c2_carried']} cast={c['c2_cast']}")
        check(c["c2_silent"] == 0, f"写入器不该留下静默沿用：{c['c2_silent']}")
        print(f"cast record OK: payload.cast={payload_of(rows[0])['cast']}；"
              f"C2 carried={c['c2_carried']} cast={c['c2_cast']} silent={c['c2_silent']}")
        store.close()


SECTIONS = {"batch": section_batch_window,
            "switch": section_switch_base,
            "epithet": section_epithet,
            "markers": section_markers,
            "cascade": section_no_accumulation,
            "memory": section_epithet_memory,
            "norm": section_epithet_normalized,
            "cap": section_epithet_cap,
            "reject": section_epithet_reject,
            "noframes": section_epithet_noframes,
            "persist": section_epithet_persist,
            "cast": section_cast_record}


def main(names: list[str] | None = None) -> None:
    for name in names or list(SECTIONS):
        SECTIONS[name]()
    print(f"\nWitness basis smoke: ALL PASS（{CHECKS} 条断言）")


if __name__ == "__main__":
    main(sys.argv[1:])
