"""M2 冒烟：见证层离线测试（FakeModel 注入，不调真实模型）。

覆盖：严格 JSON 解析、anchor/diff 切换与强制重锚、small 落库、metrics 计量、
Trajectory JSONL、解析失败重试后丢弃、KB 命中注入、process_pending 游标。
运行：python3 tests/test_m2.py
"""

import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from backseat.config import Config
from backseat.store import Store
from backseat.trajectory import Trajectory
from backseat.witness import Witness, parse_mini


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


def mk_obs(store, wall=None, ref=None):
    wall = wall or time.time()
    return store.insert("observations", ts_wall=wall, ts_mono=time.monotonic(),
                        sensor="screen", kind="screen.notable", display="primary",
                        key=f"k{wall}", ref=ref, bytes=150000)


def main() -> None:
    # --- parse_mini ---
    assert parse_mini(anchor())["type"] == "anchor"
    assert parse_mini("```json\n" + diff() + "\n```")["type"] == "diff"
    assert parse_mini("{'type':'anchor'}") is None          # 非 JSON
    assert parse_mini('{"type":"roast","diff":"x","intent":"y"}') is None  # 非法 type
    assert parse_mini('{"type":"anchor","intent":"x"}') is None            # 缺 describe
    assert parse_mini('{"type":"diff","diff":"x","intent":"y","importance":2}') is None
    print("parse_mini OK: 严格契约（fence/非法type/缺字段/importance越界全拦）")

    with tempfile.TemporaryDirectory() as td:
        store = Store(Path(td) / "t.db")
        cfg = Config(anchor_every_n=2, anchor_age_min=30)

        # --- 首帧必 anchor；之后 diff；n 超限强制重锚 ---
        m = FakeModel([anchor("终端，backseat 项目"), diff("改store.py"),
                       diff("改bus.py"), anchor("浏览器 MDN")])
        w = Witness(store, m, Trajectory(td), cfg, log=lambda *a: None)
        t = time.time()
        for i in range(4):
            w.process(store.conn.execute(
                "SELECT * FROM observations WHERE id=?", (mk_obs(store, t + i),)).fetchone())
        types = [r["type"] for r in store.conn.execute(
            "SELECT type FROM messages WHERE level='small' ORDER BY id")]
        assert types == ["anchor", "diff", "diff", "anchor"], types
        assert store.get_state("anchor")["n"] == 0  # 重锚后计数归零
        # 模式切换：第 2、3 次调用 mode=diff（anchor_every_n=2 → 第 4 次重锚）
        modes = [json.loads(c[1]["content"][0]["text"])["mode"] for c in m.calls]
        assert modes == ["anchor", "diff", "diff", "anchor"], modes
        print(f"witness OK: 双模式+强制重锚（modes={modes}）")

        # --- 计量与轨迹 ---
        assert store.conn.execute("SELECT count(*) c FROM metrics").fetchone()["c"] == 4
        traj = list((Path(td) / "trajectory").glob("*.jsonl"))
        assert len(traj) == 1
        rec = json.loads(traj[0].read_text().splitlines()[0])
        assert rec["purpose"] == "witness:anchor" and rec["usage"]["cached"] == 50
        assert rec["messages"][0]["role"] == "system"
        print("trajectory+metrics OK: 逐调用全量落盘")

        # --- 解析失败：重试 1 次后丢弃，游标仍推进 ---
        m2 = FakeModel(["不是json", "也不是json"])
        w2 = Witness(store, m2, Trajectory(td), cfg, log=lambda *a: None)
        oid = mk_obs(store, t + 100)
        w2.process(store.conn.execute(
            "SELECT * FROM observations WHERE id=?", (oid,)).fetchone())
        assert w2.stats["discarded"] == 1 and w2.stats["parse_fail"] == 2
        assert m2.calls[1] == m2.calls[0], "重试应复用同一 context"
        cnt = store.conn.execute("SELECT count(*) c FROM messages WHERE level='small' "
                                 "AND ts_start>?", (t + 99,)).fetchone()["c"]
        assert cnt == 0, "两次失败应丢弃不落库"
        print("discard OK: 解析失败重试1次后丢弃（链路 B 终点）")

        # --- KB 命中注入 ---
        store.insert("messages", level="small", ts_start=t + 150, ts_end=t + 150,
                     content="pytest 三连败，正在修外键", intent="修测试", type="diff",
                     source="vlm", obs_ids="[]")
        store.insert("kb", keys='["pytest"]', desc="修外键史", created=t, updated=t)
        m3 = FakeModel([diff("pytest 又红了")])
        w3 = Witness(store, m3, Trajectory(td), cfg, log=lambda *a: None)
        w3.process(store.conn.execute(
            "SELECT * FROM observations WHERE id=?", (mk_obs(store, t + 200),)).fetchone())
        sysmsg = m3.calls[0][0]["content"]
        assert "[KB命中行]" in sysmsg and "修外键史" in sysmsg
        print("kb hit OK: keys 命中近期事实 → 注入 context")

        # --- process_pending 游标 ---
        store.set_state("witness_cursor", 0)
        for i in range(6):  # 占位帧文件（批帧会读取）
            (Path(td) / f"c{i}.jpg").write_bytes(b"\xff\xd8fake")
        store.conn.execute("UPDATE observations SET ref=? WHERE id>? AND ref IS NULL",
                            (str(Path(td) / "c0.jpg"), 0))
        store.conn.commit()
        m4 = FakeModel([anchor()] * 10)
        w4 = Witness(store, m4, Trajectory(td), cfg, log=lambda *a: None)
        n = w4.process_pending(batch_size=100)
        total_obs = store.conn.execute(
            "SELECT count(*) c FROM observations").fetchone()["c"]
        assert n >= 1, n  # 新语义：n=LLM 调用数（6 帧一批 → 少量调用覆盖全部）
        assert store.get_state("witness_cursor") == total_obs
        n2 = w4.process_pending()
        assert n2 == 0 and store.get_state("witness_cursor") == total_obs
        print(f"cursor OK: 处理 {n} 条后游标就位，重跑幂等")

        store.close()
    print("\nM2 smoke: ALL PASS")


if __name__ == "__main__":
    main()
