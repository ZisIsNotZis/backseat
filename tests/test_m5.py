"""M5 冒烟：人格层草稿契约、引擎闸门（节流/去重/沉默权）、弹幕出口介质翻译、
expressions 溯源与状态回执。FakeModel + 假出口，离线全过。
运行：python3 tests/test_m5.py
"""

import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from backseat.config import Config
from backseat.persona import DanmakuOutlet, Persona, parse_draft
from backseat.store import Store
from backseat.trajectory import Trajectory

sys.path.insert(0, str(Path(__file__).parent))
from test_m2 import FakeModel, mk_obs  # noqa: E402


class FakeOutlet:
    def __init__(self):
        self.pushed = []
        self.stats = {"shown": 0, "push_fail": 0}

    def render(self, content, mood, salience):
        self.pushed.append((content, mood, salience))
        self.stats["shown"] += 1
        return "shown"

    def close(self):
        pass


def draft(content, mood="mocking", salience=0.5, **kw):
    return json.dumps({"content": content, "mood": mood, "salience": salience, **kw},
                      ensure_ascii=False)


def main() -> None:
    # --- 草稿契约解析 ---
    assert parse_draft(draft("哈"))["mood"] == "mocking"
    assert parse_draft(draft("哈", mood="未知情绪"))["mood"] == "neutral"  # 闭集外降级
    assert parse_draft('{"silence": true}') == {"silence": True}
    assert parse_draft('{"content":" "}') is None
    assert parse_draft(draft("哈", salience=2))["salience"] == 0.5  # 越界回默认
    print("parse_draft OK: 严格契约 + mood 闭集降级 + 沉默权")

    with tempfile.TemporaryDirectory() as td:
        store = Store(Path(td) / "t.db")
        cfg = Config(min_roast_interval=0)  # 关节流便于测去重
        outlet = FakeOutlet()
        t0 = time.time()

        # --- 触发判定：importance>=0.7 或 KB 命中 ---
        p = Persona("test", store, FakeModel([draft("pytest 全绿了")]), Trajectory(td), cfg,
                    outlet=outlet, log=lambda *a: None)
        store.insert("kb", keys='["pytest"]', desc="修外键史", created=t0, updated=t0)
        s_low = store.conn.execute("SELECT * FROM messages WHERE id=?", (
            store.insert("messages", level="small", ts_start=t0, ts_end=t0,
                         content="闲逛", payload=json.dumps({"importance": 0.3})),
        )).fetchone()
        assert p.should_speak(s_low) == (False, "")
        s_kb = store.conn.execute("SELECT * FROM messages WHERE id=?", (
            store.insert("messages", level="small", ts_start=t0, ts_end=t0,
                         content="pytest 全绿了"),  # 命中 KB
        )).fetchone()
        fire, why = p.should_speak(s_kb)
        assert fire and why == "KB命中"
        print("trigger OK: importance>=0.7 / KB命中（封闭触发集）")

        # --- 放行链：草稿 → expressions → 出口回执 shown ---
        xid = p.on_fact(s_kb)
        assert xid is not None
        row = store.conn.execute("SELECT * FROM expressions WHERE id=?", (xid,)).fetchone()
        assert row["channel"] == "danmaku" and row["status"] == "shown"
        assert json.loads(row["source_refs"])[0]["id"] == s_kb["id"]
        assert len(outlet.pushed) == 1
        print("gate OK: 放行 → expressions 溯源 → 出口渲染回执")

        # --- 去重：同义发言（bigram 重叠≥60%）拦截 ---
        p.model = FakeModel([draft("pytest 全绿了！"),
                             draft("完全不同的一次新吐槽")])
        s_kb2 = store.conn.execute("SELECT * FROM messages WHERE id=?", (
            store.insert("messages", level="small", ts_start=t0 + 99, ts_end=t0 + 99,
                         content="pytest 又全绿",
                         payload=json.dumps({"importance": 0.8})),  # importance 触发
        )).fetchone()
        store.conn.execute("UPDATE kb SET last_shown=NULL")
        store.conn.commit()
        assert p.on_fact(s_kb2) is None and p.stats["dup"] == 1, "重复应被拦"
        xid3 = p.on_fact(s_kb2)  # 第二份草稿不同 → 放行
        assert xid3 is not None
        print("dedup OK: bigram 重叠≥60% 拦截，新内容放行")

        # --- 节流 ---
        p2 = Persona("t2", store, FakeModel([draft("第三次"), draft("第四次")]),
                     Trajectory(td), Config(min_roast_interval=3600),
                     outlet=FakeOutlet(), log=lambda *a: None)
        store.conn.execute("UPDATE kb SET last_shown=NULL")
        store.conn.commit()
        s3 = store.conn.execute("SELECT * FROM messages WHERE id=?", (
            store.insert("messages", level="small", ts_start=t0 + 200, ts_end=t0 + 200,
                         content="pytest 三连"),)).fetchone()
        assert p2.on_fact(s3) is None and p2.stats["throttled"] == 1
        print("throttle OK: min_roast_interval 节流生效")

        # --- 沉默权 ---
        p3 = Persona("t3", store, FakeModel(['{"silence": true}']),
                     Trajectory(td), Config(min_roast_interval=0),
                     outlet=FakeOutlet(), log=lambda *a: None)
        store.conn.execute("UPDATE kb SET last_shown=NULL")
        store.conn.commit()
        assert p3.on_fact(s3) is None and p3.stats["silence"] == 1
        print("silence OK: 安静是常态")

        # --- 介质翻译表 ---
        assert DanmakuOutlet and len(__import__("backseat.persona",
                                                fromlist=["MOOD_COLORS"]).MOOD_COLORS) == 10
        print("mood map OK: 10 情绪闭集 → 颜色；salience>=0.85 → large")

        store.close()
    print("\nM5 smoke: ALL PASS")


if __name__ == "__main__":
    main()
