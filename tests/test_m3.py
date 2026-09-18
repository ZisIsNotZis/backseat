"""M2 批帧 + M3 压缩层离线冒烟（FakeModel 注入）。

覆盖：拼批（锚须单帧/diff 批 6 帧）、批 mini 落库与锚计数、
K条 small→mid、M条 mid→big、folded+parent_id 下钻、USER_MODEL 增量合并、
坏输出重试后跳过（链路 D）。
运行：python3 tests/test_m3.py
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
from backseat.witness import Witness

sys.path.insert(0, str(Path(__file__).parent))
from test_m2 import FakeModel, anchor, diff, mk_obs  # noqa: E402

from backseat.compressor import Compressor, parse_merge  # noqa: E402


def main() -> None:
    # --- parse_merge ---
    good = json.dumps({"content": "在写 backseat", "keys": ["backseat"],
                       "callback_tags": [], "events": [], "user_model_delta": {}})
    assert parse_merge(good)["keys"] == ["backseat"]
    assert parse_merge('{"content":"x","keys":[]}') is None  # keys 空
    assert parse_merge('{"keys":["a"]}') is None             # 缺 content
    print("parse_merge OK")

    with tempfile.TemporaryDirectory() as td:
        store = Store(Path(td) / "t.db")
        cfg = Config(anchor_every_n=100)
        t = time.time()

        # --- 拼批：首帧 anchor（单帧），后续 diff 批 6 帧/调用 ---
        m = FakeModel([anchor("锚"), diff("批1"), diff("批2")])
        w = Witness(store, m, Trajectory(td), cfg, log=lambda *a: None)
        w.compressor = None
        for i in range(13):
            (Path(td) / f"f{i}.jpg").write_bytes(b"\xff\xd8fake")  # 占位 JPEG
        obs = [store.conn.execute("SELECT * FROM observations WHERE id=?",
                                  (mk_obs(store, t + i, ref=str(Path(td) / f"f{i}.jpg")),)).fetchone()
               for i in range(13)]
        # 首帧 → anchor 单帧
        w.process_pending(batch_size=6)
        calls = len(m.calls)
        types = [r["type"] for r in store.conn.execute(
            "SELECT type FROM messages WHERE level='small' ORDER BY id")]
        assert types[0] == "anchor" and store.get_state("witness_cursor") >= 1
        # 剩余 12 帧 → 2 个 6 帧批（FakeModel 每调用消费一个输出）
        m.outputs = [diff(f"批{i}") for i in range(2)]
        w.process_pending(batch_size=6)
        types = [r["type"] for r in store.conn.execute(
            "SELECT type FROM messages WHERE level='small' ORDER BY id")]
        assert types == ["anchor", "diff", "diff"], types
        batch_rows = store.conn.execute(
            "SELECT obs_ids, ts_start, ts_end FROM messages WHERE level='small' "
            "AND type='diff' ORDER BY id").fetchall()
        assert len(json.loads(batch_rows[0]["obs_ids"])) == 6, "一批应挂 6 帧"
        assert store.get_state("anchor")["n"] == 12, "锚计数应累加批内帧数"
        print("batch OK: 锚单帧 + 2×6帧批，obs_ids 全溯源，锚计数累加")

        # --- L2：K=3 条 small → mid（USER_MODEL 合并 + 折叠下钻）---
        cfg3 = Config(chunk_k=3, epoch_m=2)
        merge_out = json.dumps({
            "content": "在写 backseat 的见证层",
            "keys": ["backseat", "见证层"],
            "callback_tags": ["批帧实现"], "events": ["冷启动修复"],
            "user_model_delta": {"projects": ["backseat"], "skill_note": "ffmpeg 输出参数坑"}},
            ensure_ascii=False)
        m2 = FakeModel([merge_out, merge_out])
        c = Compressor(store, m2, Trajectory(td), cfg3, log=lambda *a: None)
        # 现有 3 条 small（1 anchor + 2 diff）→ mid
        mid_id = c.maybe_merge("small")
        assert mid_id is not None
        kids = store.conn.execute(
            "SELECT count(*) c FROM messages WHERE parent_id=?", (mid_id,)).fetchone()["c"]
        assert kids == 3
        assert c._unfolded_count("small") == 0
        um = store.get_state("user_model")
        assert um["projects"] == ["backseat"] and "skill_note" in um, um
        print("compress small→mid OK: 折叠下钻 + USER_MODEL 浅合并")

        # --- 坏输出重试后跳过（链路 D），不折叠 ---
        m3 = FakeModel(["坏", "还是坏"])
        c2 = Compressor(store, m3, Trajectory(td), cfg3, log=lambda *a: None)
        # 只有 1 条 mid（未折叠），不足 M=2 → None；手工再插一条 mid 触发
        store.insert("messages", level="medium", ts_start=t, ts_end=t,
                     content="另一段", keys='["x"]')
        assert c2.maybe_merge("medium") is None
        assert c2.stats["skipped"] == 1 and c2.stats["parse_fail"] == 2
        assert c2._unfolded_count("medium") == 2, "跳过时不折叠"
        print("compress skip OK: 两次失败不折叠（链路 D 终点）")

        # --- mid→big ---
        m4 = FakeModel([json.dumps({"content": "backseat 元期：从零到 M3",
                                    "keys": ["backseat"], "user_model_delta": {"phase": "M3"}},
                                   ensure_ascii=False)])
        c3 = Compressor(store, m4, Trajectory(td), cfg3, log=lambda *a: None)
        big_id = c3.maybe_merge("medium")
        assert big_id is not None
        assert store.get_state("user_model")["phase"] == "M3"
        levels = [r["level"] for r in store.conn.execute(
            "SELECT level FROM messages ORDER BY id")]
        assert "big" in levels
        chain = store.conn.execute(
            "SELECT c.level FROM messages p JOIN messages c ON c.parent_id=p.id "
            "WHERE p.id=?", (big_id,)).fetchall()
        assert all(r["level"] == "medium" for r in chain) and len(chain) == 2
        print("compress mid→big OK: 汉诺塔继续爬升，全链可下钻")

        store.close()
    print("\nM3 smoke: ALL PASS")


if __name__ == "__main__":
    main()
