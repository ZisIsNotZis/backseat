"""M4 冒烟：KB 唤起注入、last_shown 防重、!always/!at 特殊键、合并边界清零、
flashback 逐字回放 + 帧引用 + 驱逐降级。
运行：python3 tests/test_m4.py
"""

import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from backseat.config import Config
from backseat.kb import KB
from backseat.store import Store
from backseat.trajectory import Trajectory

sys.path.insert(0, str(Path(__file__).parent))
from test_m2 import FakeModel, anchor, mk_obs  # noqa: E402

from backseat.witness import Witness  # noqa: E402
from backseat.compressor import Compressor  # noqa: E402


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        store = Store(Path(td) / "t.db")
        t = time.time()
        kb = KB(store)

        # --- keys 命中 + last_shown 防重 ---
        store.insert("kb", keys='["pytest"]', desc="修外键史", created=t, updated=t)
        hits = kb.hits_for("pytest 又红了 修外键")
        assert hits == ["(1) 修外键史"]
        assert kb.hits_for("pytest 再次出现") == [], "60min 内同 key 不重复注入"
        assert store.conn.execute("SELECT last_shown FROM kb WHERE id=1").fetchone()["last_shown"]
        kb.clear_last_shown()
        assert kb.hits_for("pytest 第三次") == ["(1) 修外键史"], "清零后可再浮现"
        print("kb hit OK: 命中→防重→compaction 清零再浮现")

        # --- !always 周期重放 / !at 到点 ---
        store.insert("kb", keys='[]', desc="喝水", special="!always=1",  # 每 1 分钟
                     created=t, updated=t)
        store.insert("kb", keys='[]', desc="开会提醒", special="!at=2099-01-01 09:00",
                     created=t, updated=t)
        hits = kb.hits_for("无关内容", now=t + 120)  # !always 1min 已到期
        assert any("喝水" in h for h in hits), hits
        assert kb.hits_for("无关内容", now=t + 125) == [], "周期内不重放"
        assert not any("开会" in h for h in kb.hits_for("", now=t + 125)), "未到点不提醒"
        print("special keys OK: !always 周期重放、!at 未到点不响")

        # --- flashback：命中 + 帧引用 + 驱逐降级 ---
        (Path(td) / "f1.jpg").write_bytes(b"\xff\xd8fake")
        obs1 = mk_obs(store, t, ref=str(Path(td) / "f1.jpg"))
        store.insert("messages", level="small", ts_start=t, ts_end=t,
                     content="s1", obs_ids=json.dumps([obs1]))
        store.insert("messages", level="small", ts_start=t + 1, ts_end=t + 1,
                     content="s2", obs_ids=json.dumps([obs2 := mk_obs(store, t + 1,
                                              ref=str(Path(td) / "f1.jpg"))]))
        mid = store.insert("messages", level="medium", ts_start=t, ts_end=t + 1,
                           content="修 backseat 存储层", keys=json.dumps(["backseat"]))
        for sid in (store.conn.execute("SELECT id FROM messages WHERE level='small'")
                          .fetchall()):
            store.conn.execute("UPDATE messages SET folded=1, parent_id=? WHERE id=?",
                               (mid, sid["id"]))
        store.conn.commit()

        res = kb.flashback(keys=["backseat"])
        assert len(res) == 1 and res[0]["level"] == "medium"
        assert res[0]["content"] == "修 backseat 存储层"
        assert len(res[0]["refs"]) == 2 and not res[0]["degraded"], res[0]
        print("flashback OK: keys 命中 mid，下钻 small 汇集双帧引用")

        # 驱逐一帧（模拟：obs2 ref 置空）
        store.conn.execute("UPDATE observations SET ref=NULL, ref_full=NULL WHERE id=?",
                           (obs2,))
        store.conn.commit()
        res = kb.flashback(keys=["backseat"])
        assert len(res[0]["refs"]) == 1 and res[0]["degraded"], res[0]
        print("flashback partial OK: 部分驱逐 → 剩余引用 + 降级标记")

        store.conn.execute("UPDATE observations SET ref=NULL WHERE id=?", (obs1,))
        store.conn.commit()
        res = kb.flashback(keys=["backseat"])
        assert res[0]["degraded"] and res[0]["refs"] == [], "全驱逐→文字降级"
        print("flashback degrade OK: 帧被驱逐 → 纯文字降级（链路 F）")

        # --- 端到端：见证层 context 中出现 KB 命中行（走 KB 类路径）---
        m = FakeModel([anchor("端到端锚")])
        w = Witness(store, m, Trajectory(td), Config(), log=lambda *a: None)
        w.process(store.conn.execute(
            "SELECT * FROM observations WHERE id=?", (mk_obs(store, t + 10),)).fetchone())
        assert "[KB命中行]" in m.calls[0][0]["content"]
        print("witness ctx OK: KB 命中行注入见证层 context")

        # --- 合并边界清零（压缩层挂 KB 后触发）---
        merge_out = json.dumps({"content": "c", "keys": ["k"], "user_model_delta": {}},
                               ensure_ascii=False)
        c = Compressor(store, FakeModel([merge_out]), Trajectory(td),
                       Config(chunk_k=1), log=lambda *a: None)
        store.set_state("anchor", {"ts_wall": t, "n": 1})
        c.maybe_merge("small")
        assert store.conn.execute(
            "SELECT last_shown FROM kb WHERE id=1").fetchone()["last_shown"] is None
        print("boundary OK: 合并边界 last_shown 清零")

        store.close()
    print("\nM4 smoke: ALL PASS")


if __name__ == "__main__":
    main()
