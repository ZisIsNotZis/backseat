"""M0 冒烟：总线去重、五张表、messages 汉诺塔合并、会话流视图、state kv、表达溯源。"""

import json
import tempfile
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from backseat.bus import Event, EventBus, EventKind
from backseat.clock import Stamp
from backseat.config import Config, load_config
from backseat.store import Store


def main() -> None:
    # --- 配置：弹性预算 -> 派生参数 ---
    cfg = Config()
    d = cfg.derived()
    assert d["retention_days"] == 20 and d["l1_cadence_s"] == 20.0
    cfg2 = Config(storage_bytes=200 * 1024**3, effort=0.9, daily_requests=8640)
    d2 = cfg2.derived()
    assert d2["retention_days"] == 200 and d2["detective_enabled"] and d2["l1_cadence_s"] == 10.0
    print(f"derived(default)={d}")
    print(f"derived(200G, effort=0.9)={d2}")

    # --- 总线去重 ---
    bus = EventBus(dedup_window=1.0)
    seen: list[Event] = []
    bus.subscribe(seen.append)
    s = Stamp.now()
    assert bus.publish_sync(Event(EventKind.WINDOW_SWITCH, "term", s)) is True
    assert bus.publish_sync(Event(EventKind.WINDOW_SWITCH, "term", s)) is False
    assert bus.publish_sync(Event(EventKind.WINDOW_SWITCH, "browser", s)) is True
    assert len(seen) == 2
    print("bus dedup OK")

    # --- 五张表：插件无关性 + 汉诺塔合并 ---
    with tempfile.TemporaryDirectory() as td:
        store = Store(Path(td) / "backseat.db")
        obs1 = store.insert("observations", ts_wall=s.wall, ts_mono=s.mono,
                            sensor="screen", kind="screen.notable", display="primary", key="ab" * 8,
                            ref="/tmp/f.jpg", hash="ab" * 8, bytes=150000,
                            meta={"display": "primary", "trigger": "window_switch"})
        obs2 = store.insert("observations", ts_wall=s.wall + 1, ts_mono=s.mono + 1,
                            sensor="dbus", kind="dbus.notification", display="primary",
                            content={"app": "pytest", "summary": "3 tests failed"})

        # small 消息 ×3
        sm = []
        for i in range(3):
            sm.append(store.insert("messages", level="small",
                                   ts_start=s.wall + i, ts_end=s.wall + i,
                                   content=f"change {i}", intent="修外键", type="diff",
                                   source="vlm", payload={"i": i},
                                   obs_ids=json.dumps([obs1 if i == 0 else obs2])))
        # 汉诺塔：3 small 折叠成 1 medium
        mid = store.insert("messages", level="medium",
                           ts_start=s.wall, ts_end=s.wall + 2,
                           content="终端里跑测试连续失败，在修外键",
                           keys=json.dumps(["pytest", "外键"]),
                           user_model_delta={"project": "backseat"})
        for i in sm:
            store.conn.execute("UPDATE messages SET folded=1, parent_id=? WHERE id=?", (mid, i))
        # 1 big 吸收 medium（演示层级可继续爬）
        big = store.insert("messages", level="big", ts_start=s.wall, ts_end=s.wall,
                           content="backseat 项目第一天", keys=json.dumps(["backseat"]))
        store.conn.execute("UPDATE messages SET folded=1, parent_id=? WHERE id=?", (big, mid))
        store.conn.commit()

        # 会话流视图：small 且未折叠
        stream = store.session_stream()
        assert len(stream) == 0  # 全被折叠了
        # 下钻：medium 的三个孩子还在
        kids = store.conn.execute(
            "SELECT count(*) c FROM messages WHERE parent_id=?", (mid,)).fetchone()["c"]
        assert kids == 3
        # 溯源链：big -> medium -> small -> observations
        chain = store.conn.execute("""
            SELECT c.level FROM messages p
            JOIN messages c ON c.parent_id = p.id WHERE p.id=?""", (big,)).fetchall()
        assert chain[0]["level"] == "medium"
        print("messages hanoi merge OK (big->medium->small->obs 全链可下钻)")

        # kb / expressions / state
        store.insert("kb", keys='["backseat", "记忆引擎"]', desc="用户在做 backseat 项目",
                     kind="fact", created=s.wall, updated=s.wall)
        e1 = store.insert("expressions", ts_wall=s.wall + 3, kind="speak",
                          content="测试连挂三次，代码已经开始嘲讽你了", channel="danmaku",
                          mood="mocking", salience=0.6, dedup_key="测试连挂三次",
                          source_refs=[{"table": "messages", "id": mid}], status="shown")
        e2 = store.insert("expressions", ts_wall=s.wall + 4, kind="propose",
                          content="要不要我加个测试前自动 lint？", channel="notification",
                          status="sent")
        store.set_state("user_model", {"projects": ["backseat"]})
        store.conn.commit()
        row = store.conn.execute("SELECT * FROM expressions WHERE id=?", (e1,)).fetchone()
        refs = json.loads(row["source_refs"])
        assert refs[0]["table"] == "messages" and refs[0]["id"] == mid
        assert row["mood"] == "mocking" and row["channel"] == "danmaku"
        p = store.conn.execute("SELECT status FROM expressions WHERE id=?", (e2,)).fetchone()
        assert p["status"] == "sent"
        assert store.storage_used() == 150000
        assert store.get_state("user_model")["projects"] == ["backseat"]
        store.close()
    print("store OK: 5 tables, plugin-agnostic, hanoi-provenance traceable")

    # --- TOML 加载 ---
    c3 = load_config(Path(__file__).parent.parent / "config.example.toml")
    assert c3.storage_bytes == 20 * 1024**3 and c3.model.startswith("volcengine/")
    print("config load OK")

    print("\nM0 smoke: ALL PASS")


if __name__ == "__main__":
    main()
