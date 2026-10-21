"""见证层证据基冒烟（批窗口 / 切换句基帧 / 通行称呼）：一行只写它自己 obs_ids 撑得住的话。

SCHEMA §语义层证据引用 的三条写入规则各一节；它们不是门槛，门槛住 ROADMAP，由项目自己的节点判。

覆盖：
a) 批窗口取被引用观察的 min/max：`id` 与 `ts_wall` 不同序时不再产生倒置窗口、不再有
   落在自窗外的 obs_ids 成员（旧写法 ts_start=rows[0].ts_wall、ts_end=rows[-1].ts_wall
   在这种输入上窗口倒置且成员出窗——本测试把旧写法的结果也断言出来，判别力不靠注释）；
b) `从X切至Y` 切换句把被切离端并进本行 obs_ids：本轮帧 + 基帧，窗口覆盖过渡两端；
   模型点名 since 时以点名为准（可指到上上条），没有切换句的行不连带基帧；
c) 通行称呼只认本轮点名的图：上一行用过的称呼没点名就拒绝（正文删掉、payload 记
   epithet_refused、stats 计数可见），点名图号越界同样拒绝，点名有效则保留，
   无帧可看的行一律拒绝，新称呼不被误删、被沿用后同样被拒，删空的整行不落库；
运行：python3 tests/test_witness_basis.py [batch|switch|epithet]
      （不带参数跑全部三节；分节是为了能在旧代码上单独跑出批窗口的红）
"""

import json
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


# —— c) 通行称呼：只认本轮点名的图 ——
def section_epithet() -> None:
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
            # 2) 上一行用过的称呼，本轮没点名 → 拒绝
            diff(f"{name}仍在原地", intent="跳舞"),
            # 3) 点名本轮第 1 张图 → 保留
            diff(f"{name}在蓝砖拱廊下跳街舞", intent="跳舞",
                 cast=[{"name": name, "frame": 1}]),
        ])
        r1 = w.process(obs_row(store, o1))
        check(name in store.conn.execute("SELECT content FROM messages WHERE id=?",
                                        (r1,)).fetchone()["content"],
              "点名了本轮图的称呼应保留")
        check(name in store.get_state("epithets", []), "用过的称呼应进册，供下一行核对")

        r2 = w.process(obs_row(store, o2))
        row2 = store.conn.execute("SELECT * FROM messages WHERE id=?", (r2,)).fetchone()
        check(name not in row2["content"],
              f"没点名的沿用称呼不得进正文：{row2['content']!r}")
        check(row2["content"].strip() == "仍在原地", f"删掉称呼后剩下的应是事实本身：{row2['content']!r}")
        check(payload_of(row2).get("epithet_refused"),
              f"拒绝要留痕（payload.epithet_refused）：{payload_of(row2)}")
        check(w.stats.get("epithet_refused", 0) >= 1, f"拒绝要计数：{w.stats}")
        check(store.get_state("epithets", []) == [], "被拒的称呼不该继续留在册里")

        r3 = w.process(obs_row(store, o3))
        row3 = store.conn.execute("SELECT * FROM messages WHERE id=?", (r3,)).fetchone()
        check(name in row3["content"], f"点名有效的称呼应保留：{row3['content']!r}")
        check(not payload_of(row3).get("epithet_refused"),
              f"保留时不该记拒绝：{payload_of(row3)}")

        # 4) 批行：点名图号越界（本轮只有 2 张图，点名第 3 张）→ 拒绝
        w.model.outputs.append(diff(f"{name}站着", cast=[{"name": name, "frame": 3}]))
        r4 = w.process_batch([obs_row(store, o4a), obs_row(store, o4b)])
        row4 = store.conn.execute("SELECT * FROM messages WHERE id=?", (r4,)).fetchone()
        check(name not in row4["content"], f"越界图号等于没点名：{row4['content']!r}")
        check(payload_of(row4).get("epithet_refused"),
              f"越界拒绝也要留痕：{payload_of(row4)}")

        # 5) 重新点名有效 → 保留，称呼回册
        w.model.outputs.append(diff(f"{name}看着屏幕", cast=[{"name": name, "frame": 1}]))
        r5 = w.process(obs_row(store, o5))
        row5 = store.conn.execute("SELECT * FROM messages WHERE id=?", (r5,)).fetchone()
        check(name in row5["content"] and name in store.get_state("epithets", []),
              "保留下来的称呼应重新进册")

        # 6) 正文只剩称呼本身 → 删空即整行不落库（落一行空话比不落更糟）
        w.model.outputs.append(diff(name))
        before = store.conn.execute("SELECT count(*) c FROM messages").fetchone()["c"]
        r6 = w.process(obs_row(store, o6))
        after = store.conn.execute("SELECT count(*) c FROM messages").fetchone()["c"]
        check(r6 is None and after == before,
              f"删空的行应丢弃、不落库：r={r6} {before}->{after}")
        check(w.stats["discarded"] >= 1, f"丢弃要计数：{w.stats}")

        # 7) 新称呼（上一行没用过）不该被误删；8) 它被沿用后同样被拒
        w.model.outputs.append(diff("红发男子在麦克风前"))
        r7 = w.process(obs_row(store, o7))
        row7 = store.conn.execute("SELECT * FROM messages WHERE id=?", (r7,)).fetchone()
        check(row7["content"] == "红发男子在麦克风前", f"新称呼不该被动：{row7['content']!r}")
        check(any("红发男子" in f for f in store.get_state("epithets", [])),
              f"新称呼应进册：{store.get_state('epithets', [])}")
        w.model.outputs.append(diff("红发男子仍在麦克风前"))
        r8 = w.process(obs_row(store, o8))
        row8 = store.conn.execute("SELECT * FROM messages WHERE id=?", (r8,)).fetchone()
        check("红发男子" not in row8["content"],
              f"沿用而来的新称呼同样要拒：{row8['content']!r}")

        # 9) 无帧可看（ref 被驱逐）→ 任何人物称呼都没有依据，点名也不算
        w.model.outputs.append(diff(f"{name}在走动", cast=[{"name": name, "frame": 1}]))
        r9 = w.process(obs_row(store, o9))
        row9 = store.conn.execute("SELECT * FROM messages WHERE id=?", (r9,)).fetchone()
        check("男子" not in row9["content"],
              f"没有帧的行不许有站立描述：{row9['content']!r}")
        check(payload_of(row9).get("epithet_refused"),
              f"无帧拒绝也要留痕：{payload_of(row9)}")
        print(f"epithet OK: 沿用被拒（正文 {row2['content']!r}）、点名有效保留、"
              f"越界/无帧拒绝、新称呼不误删、删空即丢行")

        # 机械登记：后缀变体保证"穿浅色外套的男子"与"浅色外套男子"是同一个称呼
        from backseat.witness import epithet_forms
        check({"浅色外套男子", "外套男子"} <= set(epithet_forms("穿浅色外套的男子在跳舞")),
              f"应登记后缀变体：{epithet_forms('穿浅色外套的男子在跳舞')}")
        check(epithet_forms("终端里只有报错") == [], "没有人物称呼就没有候选")
        store.close()


SECTIONS = {"batch": section_batch_window,
            "switch": section_switch_base,
            "epithet": section_epithet}


def main(names: list[str] | None = None) -> None:
    for name in names or list(SECTIONS):
        SECTIONS[name]()
    print(f"\nWitness basis smoke: ALL PASS（{CHECKS} 条断言）")


if __name__ == "__main__":
    main(sys.argv[1:])
