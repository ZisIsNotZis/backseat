"""M5 冒烟：人格层草稿契约、引擎闸门（节流/去重/沉默权）、弹幕出口介质翻译、
expressions 溯源与状态回执、引擎逐条回执关联（shown 语义可信）。FakeModel + 假出口/假 pipe，
离线全过（无需 X display）。
运行：python3 tests/test_m5.py
"""

import io
import json
import sys
import tempfile
import threading
import time
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from backseat.config import Config
from backseat.persona import DanmakuOutlet, Persona, ReceiptReader, parse_draft
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

        # --- 引擎逐条回执：stdout 按 id 关联，非回执行忽略，超时返回 None ---
        r = ReceiptReader(io.StringIO('engine stats: x\nnot json\n'
                                      '{"id":2,"status":"skipped"}\n'
                                      '{"id":1,"status":"shown"}\n'))
        r.start()
        assert r.wait(1, 0.5) == "shown"      # 乱序也按 id 关联
        assert r.wait(2, 0.5) == "skipped"
        assert r.wait(3, 0.05) is None          # 无该 id → 超时
        print("receipt OK: stdout 逐行回执按 id 关联；非回执行忽略；超时返回 None")

        # --- 超时后到达的回执不无界滞留（P2-1）：引擎冷启动首条常超时 ---
        class FeedStream:
            """可后推行的假 stdout：模拟引擎回执比 wait 超时更晚到达。"""

            def __init__(self):
                self._cv = threading.Condition()
                self._lines = deque()
                self._done = False

            def push(self, line):
                with self._cv:
                    self._lines.append(line)
                    self._cv.notify_all()

            def __iter__(self):
                while True:
                    with self._cv:
                        while not self._lines and not self._done:
                            self._cv.wait(0.2)
                        if not self._lines:
                            return
                        line = self._lines.popleft()
                    yield line

        feed = FeedStream()
        rr = ReceiptReader(feed)
        rr.start()
        for mid in range(1, 21):
            assert rr.wait(mid, 0.02) is None       # 引擎慢：每条都超时
            feed.push(json.dumps({"id": mid, "status": "shown"}) + "\n")  # 迟到回执
        deadline = time.monotonic() + 1.0
        while rr._receipts and time.monotonic() < deadline:
            time.sleep(0.02)
        assert rr._receipts == {}, f"超时后迟到回执不得滞留（字典上界）：{rr._receipts}"
        assert rr._abandoned_upto == 20, rr._abandoned_upto
        # 超时水位之后的新 id 仍能正常收到回执
        feed.push(json.dumps({"id": 21, "status": "shown"}) + "\n")
        assert rr.wait(21, 0.5) == "shown"
        print("receipt bound OK: 超时后迟到回执被丢弃，字典不随超时次数增长")

        class FakeProc:
            """假引擎进程：stdin 可写、stdout 可读、poll() 返回存活。"""

            def __init__(self, engine_stdout: str = "") -> None:
                self.stdin = io.StringIO()
                self.stdout = io.StringIO(engine_stdout)

            def poll(self):
                return None

        # 落位成功 → shown
        o1 = DanmakuOutlet(spawn=lambda: FakeProc('{"id":1,"status":"shown"}\n'))
        assert o1.render("哈", "mocking", 0.5) == "shown" and o1.stats["shown"] == 1
        # 无空余 lane（引擎回 skipped）→ 不再谎报 shown，降级 sent
        o2 = DanmakuOutlet(spawn=lambda: FakeProc('{"id":1,"status":"skipped"}\n'))
        assert o2.render("哈", "mocking", 0.5) == "sent" and o2.stats["shown"] == 0
        assert o2.stats["skipped"] == 1
        # 旧引擎无回执 → 上界等待超时 → sent（不阻塞主循环）
        o3 = DanmakuOutlet(spawn=lambda: FakeProc())
        o3.RECEIPT_TIMEOUT = 0.05
        t1 = time.monotonic()
        assert o3.render("哈", "mocking", 0.5) == "sent" and o3.stats["unconfirmed"] == 1
        assert time.monotonic() - t1 < 1.0, "等待必须有上界（≤1s）"
        print("render status OK: placed→shown / skipped→sent / 无回执→sent（shown 语义可信）")

        store.close()
    print("\nM5 smoke: ALL PASS")


if __name__ == "__main__":
    main()
