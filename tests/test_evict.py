"""存储驱逐冒烟（DESIGN §8）：L0 FIFO 最旧优先、豁免（pin / ANCHOR 引用帧 /
ref_full 全分辨率副本）、行保留 ref=NULL、水位达标、幂等、缺失文件健壮。

运行：python3 tests/test_evict.py
"""

import base64
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from backseat.store import Store

# 最小合法 1x1 JPEG（真 JPEG 魔数/结构，作占位）
JPEG_1PX = base64.b64decode(
    "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0a"
    "HBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAFAABAAAAAAAA"
    "AAAAAAAAAAAACf/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AKp//2Q==")


def jpeg(path) -> str:
    Path(path).write_bytes(JPEG_1PX)
    return str(path)


def mk_obs(store, wall, ref, bytes_, pinned=0, ref_full=None) -> int:
    return store.insert("observations", ts_wall=wall, ts_mono=wall,
                        sensor="screen", kind="screen.notable", display="primary",
                        key=f"k{wall}", ref=ref, ref_full=ref_full,
                        bytes=bytes_, pinned=pinned)


def main() -> None:
    # --- 场景 1：FIFO 最旧非豁免优先 + pin/ref_full 豁免 + 行保留 ---
    with tempfile.TemporaryDirectory() as td:
        store = Store(Path(td) / "t.db")
        t = 1000.0
        f1, f2 = jpeg(Path(td) / "f1.jpg"), jpeg(Path(td) / "f2.jpg")
        f2full = jpeg(Path(td) / "f2full.jpg")
        f3, f4 = jpeg(Path(td) / "f3.jpg"), jpeg(Path(td) / "f4.jpg")
        # 每条 500B，预算 1000 → 从头扫：前两条豁免，后两条被驱逐恰好达标
        o1 = mk_obs(store, t + 0, f1, 500, pinned=1)            # 最旧，pin 豁免
        o2 = mk_obs(store, t + 1, f2, 500, ref_full=f2full)     # ref_full 豁免
        o3 = mk_obs(store, t + 2, f3, 500)                      # 最旧可驱逐
        o4 = mk_obs(store, t + 3, f4, 500)                      # 次旧可驱逐
        assert store.storage_used() == 2000

        ev = store.evict_l0(1000)
        assert ev["evicted"] == 2 and ev["freed_bytes"] == 1000, ev
        assert ev["exempt"] == 2, ev
        assert ev["used_after"] == 1000 and ev["used_after"] <= 1000, ev
        # 驱逐只置 ref=NULL（保留行），文件被删
        rows = {r["id"]: r for r in store.conn.execute(
            "SELECT id, ref, ref_full FROM observations ORDER BY id")}
        assert len(rows) == 4, "驱逐保留行（时间线/降级为纯文字）"
        assert rows[o3]["ref"] is None and rows[o4]["ref"] is None
        assert not Path(f3).exists() and not Path(f4).exists()
        # 豁免两项原样保留（pin 的 ref；ref_full 的 ref + 全分辨率副本）
        assert rows[o1]["ref"] == f1 and Path(f1).exists()
        assert rows[o2]["ref"] == f2 and rows[o2]["ref_full"] == f2full
        assert Path(f2).exists() and Path(f2full).exists()
        print("evict FIFO OK: 最旧非豁免被删、pin/ref_full 豁免、行保留 ref=NULL")

        # --- 幂等/可重入：同预算重入应无动作 ---
        ev2 = store.evict_l0(1000)
        assert ev2["evicted"] == 0 and ev2["freed_bytes"] == 0, ev2
        assert ev2["used_after"] == store.storage_used() == 1000, ev2
        print("evict idempotent OK: 达标后重入无动作")

        # --- 预算已满足：不动 ---
        ev3 = store.evict_l0(10_000)
        assert ev3["evicted"] == 0 and ev3["used_before"] == ev3["used_after"], ev3
        store.close()

    # --- 场景 2：ANCHOR 引用帧豁免（最旧也不驱逐）；换锚后解除 ---
    with tempfile.TemporaryDirectory() as td:
        store = Store(Path(td) / "t.db")
        t = 2000.0
        fa, fb = jpeg(Path(td) / "a.jpg"), jpeg(Path(td) / "b.jpg")
        oa = mk_obs(store, t + 0, fa, 500)   # 最旧，被最近 anchor 引用 → 豁免
        ob = mk_obs(store, t + 1, fb, 500)   # 次旧，可驱逐
        store.insert("messages", level="small", ts_start=t, ts_end=t,
                     content="锚", type="anchor", obs_ids=json.dumps([oa]))
        assert store.storage_used() == 1000

        ev = store.evict_l0(500)
        assert ev["evicted"] == 1 and ev["freed_bytes"] == 500, ev
        assert ev["exempt"] == 1, ev
        assert ev["used_after"] == 500 and ev["used_after"] <= 500, ev
        ra = store.conn.execute("SELECT ref FROM observations WHERE id=?", (oa,)).fetchone()
        rb = store.conn.execute("SELECT ref FROM observations WHERE id=?", (ob,)).fetchone()
        assert ra["ref"] == fa and Path(fa).exists(), "anchor 引用帧不得驱逐"
        assert rb["ref"] is None and not Path(fb).exists()
        print("evict anchor OK: 最近 anchor 引用帧豁免，其余按 FIFO 驱逐")

        # 新 anchor 改引用 ob → oa 不再受保护，重获可驱逐
        store.insert("messages", level="small", ts_start=t + 2, ts_end=t + 2,
                     content="新锚", type="anchor", obs_ids=json.dumps([ob]))
        ev2 = store.evict_l0(0)
        assert ev2["evicted"] == 1 and ev2["used_after"] == 0, ev2
        assert not Path(fa).exists(), "换锚后旧引用帧解除保护"
        store.close()
        print("evict re-anchor OK: 只有最近 anchor 的引用帧受保护")

    # --- 场景 3：缺失文件健壮 + ref=NULL 跳过 ---
    with tempfile.TemporaryDirectory() as td:
        store = Store(Path(td) / "t.db")
        t = 3000.0
        gone = str(Path(td) / "gone.jpg")  # 从不创建
        live = jpeg(Path(td) / "live.jpg")
        mk_obs(store, t + 0, gone, 400)    # 文件已缺失
        mk_obs(store, t + 1, live, 400)
        norf = store.insert("observations", ts_wall=t + 2, ts_mono=t + 2, sensor="dbus",
                            kind="dbus.notification", display="primary",
                            content={"a": 1}, bytes=300)  # ref=NULL，不在候选内
        assert store.storage_used() == 1100

        ev = store.evict_l0(400)
        assert ev["evicted"] == 2 and ev["freed_bytes"] == 800, ev
        assert ev["missing_files"] == 1, ev
        assert ev["used_after"] == 300 and ev["used_after"] <= 400, ev
        assert not Path(live).exists()
        r = store.conn.execute("SELECT ref, bytes FROM observations WHERE id=?",
                               (norf,)).fetchone()
        assert r["ref"] is None and r["bytes"] == 300, "ref=NULL 行保持原样"
        print("evict robust OK: 缺失文件不报错计入统计；ref=NULL 跳过")

    # --- 场景 4：全豁免时收敛（预算不可达也不死循环/无谓删除）---
    with tempfile.TemporaryDirectory() as td:
        store = Store(Path(td) / "t.db")
        fp = jpeg(Path(td) / "p.jpg")
        mk_obs(store, 4000.0, fp, 500, pinned=1)
        ev = store.evict_l0(0)  # 唯一帧是 pin → 无可驱逐对象
        assert ev["evicted"] == 0 and ev["exempt"] == 1, ev
        assert ev["used_after"] == 500, ev
        assert Path(fp).exists()
        store.close()
        print("evict converge OK: 全豁免时退出收敛、不删文件")

    print("\nM-evict smoke: ALL PASS")


if __name__ == "__main__":
    main()
