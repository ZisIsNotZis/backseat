"""洞察挖掘器 + 人格双模式冒烟（离线）。

验证：跨时间洞察（重复/复现/连续）检测；assistant 模式无洞察则沉默（含周期 digest）；
companion 模式保留原行为（氛围组，显式开启）。
运行：python3 tests/test_insight.py
"""

import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from backseat.config import Config
from backseat.insight import InsightMiner
from backseat.persona import Persona
from backseat.store import Store
from backseat.trajectory import Trajectory
from test_m2 import FakeModel


def draft(content, mood="neutral", salience=0.5):
    return json.dumps({"content": content, "mood": mood, "salience": salience},
                      ensure_ascii=False)


def session_insight() -> None:
    """连续会话起点必须由 observations 时间线推导（跨停机 gap 自愈，不依赖陈旧 state）。"""
    cfg = Config()
    thresh = cfg.gap_close_min * 60  # 30min
    now0 = time.time()

    def add_obs(store, base, span, step=600):
        for i in range(int(span // step) + 1):
            t = base + i * step
            store.insert("observations", ts_wall=t, ts_mono=t, sensor="screen",
                         kind="screen.notable")

    # 用例1：段 t0..t0+2h → gap 100h → 段 t1..t1+1h；连续会话应为 1h（而非 103h）
    with tempfile.TemporaryDirectory() as td:
        store = Store(Path(td) / "a.db")
        t0 = now0 - 103 * 3600
        add_obs(store, t0, 2 * 3600)                 # 段 A：2h
        t1 = t0 + 2 * 3600 + 100 * 3600              # 停机 100h
        add_obs(store, t1, 1 * 3600)                 # 段 B：1h
        now = t1 + 3600                              # 当前时刻=最新观察
        # 陈旧持久化 state（旧实现依赖它 → 会算成 103h）：新算法必须忽略
        store.set_state("session_start_wall", t0)
        miner = InsightMiner(store, cfg)
        assert now - miner._session_start(now) == 3600, miner._session_start(now)
        cands = miner.candidates(now=now, limit=5)
        assert not any(c["kind"] == "long_session" for c in cands), cands
        print("session OK: 跨 100h 停机 gap 后连续会话=1h（陈旧 state 被忽略）")
        store.close()

    # 用例2：连续 7h → long_session 触发（阈值 6h 不变）
    with tempfile.TemporaryDirectory() as td:
        store = Store(Path(td) / "b.db")
        t0 = now0 - 7 * 3600
        add_obs(store, t0, 7 * 3600, step=900)       # 7h，15min 间隔
        now = t0 + 7 * 3600
        cands = InsightMiner(store, cfg).candidates(now=now, limit=5)
        ls = [c for c in cands if c["kind"] == "long_session"]
        assert ls and "7.0 小时" in ls[0]["detail"], cands
        print("session OK: 连续 7h → long_session 触发:", ls[0]["detail"])
        store.close()

    # 用例3：边界——gap 恰等于阈值不断（同一会话）；略大即断
    with tempfile.TemporaryDirectory() as td:
        base = now0 - 3 * thresh
        eq = Store(Path(td) / "eq.db")
        for t in (base, base + thresh):
            eq.insert("observations", ts_wall=t, ts_mono=t, sensor="screen",
                      kind="screen.notable")
        assert InsightMiner(eq, cfg)._session_start(base + thresh) == base, \
            "gap 恰等于阈值应视为同一会话"
        eq.close()
        over = Store(Path(td) / "over.db")
        for t in (base, base + thresh + 1):
            over.insert("observations", ts_wall=t, ts_mono=t, sensor="screen",
                        kind="screen.notable")
        assert InsightMiner(over, cfg)._session_start(base + thresh + 1) == base + thresh + 1, \
            "gap 超阈值应断开"
        over.close()
        print("session OK: 边界 gap==阈值不断，>阈值即断")


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        store = Store(Path(td) / "t.db")
        now = time.time()
        day = 86400

        store.insert("kb", keys='["backseat"]', desc="在写 backseat", created=now, updated=now)
        store.insert("kb", keys='["pytest"]', desc="修外键史", created=now - 2 * day,
                     updated=now - 2 * day)
        # 主题「backseat」横跨 3 天、出现 3 次 → repeat + streak
        for i, off in enumerate((3 * day, 2 * day, 1 * day)):
            store.insert("messages", level="medium", ts_start=now - off,
                         ts_end=now - off, content=f"第{i + 1}段", keys='["backseat"]')
        # 「pytest」久未出现（2 天前）→ revival
        store.insert("messages", level="medium", ts_start=now - 2 * day,
                     ts_end=now - 2 * day, content="旧事", keys='["pytest"]')
        store.insert("messages", level="medium", ts_start=now - 3 * day,
                     ts_end=now - 3 * day, content="旧事2", keys='["pytest"]')

        miner = InsightMiner(store, Config())
        cands = miner.candidates(now=now, limit=5)
        kinds = {c["kind"] for c in cands}
        topics = {c["topic"] for c in cands}
        assert "repeat" in kinds and "backseat" in topics, cands
        assert "revival" in kinds and "pytest" in topics, cands
        detail = next(c for c in cands if c["kind"] == "repeat")["detail"]
        assert "第 3 次" in detail and "3 天" in detail, detail
        print("miner OK:", " | ".join(c["detail"] for c in cands[:3]))

        # assistant 模式：有洞察 → 说；无洞察 → 静默（digest 也不发）
        outlet = type("O", (), {"stats": {"shown": 0, "push_fail": 0},
                                "render": lambda s, c, m, sal: "shown",
                                "close": lambda s: None})()
        p = Persona("a", store, FakeModel([draft("第 3 天了，我数着呢")]),
                    Trajectory(td), Config(persona_mode="assistant"), outlet=outlet,
                    log=lambda *a: None)
        sid = store.insert("messages", level="small", ts_start=now, ts_end=now,
                           content="屏幕上的东西")
        row = store.conn.execute("SELECT * FROM messages WHERE id=?", (sid,)).fetchone()
        assert p.on_fact(row) is not None, "有洞察应开口"
        assert "[洞察候选]" in p.model.calls[0][0]["content"]
        assert "禁止复述" in p.model.calls[0][0]["content"]
        print("assistant OK: 有洞察→开口，context 含洞察候选且契约禁止复述")

        # 无洞察：清空 keys 后 digest 应静默
        store.conn.execute("UPDATE messages SET keys=NULL")
        store.conn.execute("UPDATE kb SET keys='[\"不存在的词\"]'")
        store.conn.commit()
        p2 = Persona("a2", store, FakeModel([draft("不该说")]), Trajectory(td),
                     Config(persona_mode="assistant"), outlet=outlet, log=lambda *a: None)
        assert p2.on_digest() is None and p2.stats["silence"] == 1
        print("assistant silence OK: 无洞察时 digest 也沉默（不说话 > 说废话）")

        # companion 模式：显式开启才有氛围组行为
        p3 = Persona("c", store, FakeModel([draft("看剧呢~")]), Trajectory(td),
                     Config(persona_mode="companion", min_roast_interval=0), outlet=outlet, log=lambda *a: None)
        assert p3.on_digest() is not None
        print("companion OK: 气氛组作为显式模式保留")

        # 洞察优先级：同 topic 只留最有价值的一条
        cands2 = InsightMiner(store, Config()).candidates(now=now, limit=5)
        bs = [c for c in cands2 if c["topic"] == "backseat"]
        assert len(bs) <= 1, bs
        store.close()
    session_insight()
    print("\nInsight smoke: ALL PASS")


if __name__ == "__main__":
    main()
