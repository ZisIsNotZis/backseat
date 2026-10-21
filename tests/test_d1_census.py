"""D1 普查尺子的冒烟：A 的三类缺陷、C1 的基帧、C2 的沿用口径，以及判定人口的三种退出码。

为什么要有它：`scripts/d1_census.py` 是 M0′ 门槛 D1 的判定程序（口径住 SCHEMA「门槛判定程序」）。
一个判据脚本自己没被断言过，就等于把门槛交给一段没人跑过的代码。本测试用合成库把每类
缺陷各造一行，断言它被数出来、且不被多算；再断言"判定人口为空"退出 2 而不是 0——
缺值记不能确立，不记通过。

运行：python3 tests/test_d1_census.py
"""

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import d1_census

from backseat.store import Store

CHECKS = 0


def check(cond, msg) -> None:
    global CHECKS
    CHECKS += 1
    assert cond, msg


def build(db: Path) -> None:
    """合成库：obs 的 id 与 ts_wall 不同序（真实库里 id/ts 不同序是 D1 A 的老病根）。"""
    st = Store(str(db))
    # 观察：id 1..6，墙钟故意不随 id 单调
    walls = {1: 100.0, 2: 106.0, 3: 101.0, 4: 130.0, 5: 131.0, 6: 200.0}
    for i, w in walls.items():
        st.insert("observations", id=i, ts_wall=w, ts_mono=w, sensor="screen",
                  kind="screen.notable", ref=f"/frames/{i}.jpg", bytes=90000)
    rows = [
        # id, ts_start, ts_end, content, payload, obs_ids
        (1, 100.0, 101.0, "浅色外套男子站在拱廊前", None, [1]),               # 首次称呼：不算沿用
        (2, 99.0, 101.0, "从海边切至拱廊，画面变亮", None, [1]),              # C1 没记基帧
        (3, 100.0, 101.0, "浅色外套男子仍在原地", None, [1]),                 # 跨行沿用（隔一行）
        (4, 130.0, 131.0, "从海边切至拱廊", {"switch_base": [5]}, [4, 5]),   # C1 合规
        (5, 130.0, 131.0, "从海边切至拱廊", {"switch_base": [6]}, [4]),      # 基帧不在 obs_ids
        (6, 101.0, 100.0, "倒置窗口的一行", None, [2]),                       # 窗口倒置
        (7, 130.0, 131.0, "浅色外套男子在原地",
         {"cast": [{"name": "浅色外套男子", "frame": 1}]}, [4]),             # C2 点名了：合规
        (8, 130.0, 131.0, "浅色外套男子又出现", None, [4]),                   # 与上一行同词面：相邻沿用
    ]
    for mid, a, b, content, payload, ids in rows:
        st.insert("messages", id=mid, level="small", ts_start=a, ts_end=b, content=content,
                  payload=json.dumps(payload, ensure_ascii=False) if payload else None,
                  obs_ids=json.dumps(ids))
    st.conn.commit()
    st.close()


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="d1census-"))
    db = tmp / "s.db"
    build(db)
    con = d1_census.connect(str(db))
    rows, walls = d1_census.load(con)

    # A：id 6 倒置且 obs 2 的 106 出窗；其余行的引用都在自窗内
    a = d1_census.census_a([r for r in rows if r["id"] <= 6], walls)
    check(a["rows"] == 6, "A 人口应为 6 行")
    check(a["refs"] == 7, f"A 引用应为 7 条，得 {a['refs']}")
    check(a["missing_ids"] == 0, "合成库里没有坏 id")
    check(a["inverted_windows"] == 1, "倒置窗口应恰好 1 条（id 6）")
    check(a["rows_with_outside"] == 1, "出窗行应恰好 1 条（id 6）")
    check(a["defect_rows"] == [6], f"缺陷行应为 [6]，得 {a['defect_rows']}")

    # C：C1 数词面命中的行，C2 只数「被沿用」的称呼
    c = d1_census.census_c(rows)
    check(c["c1_rows"] == 3, f"对比句行应为 3 条（id 2/4/5），得 {c['c1_rows']}")
    check(c["c1_recorded"] == 1, "只有 id 4 记明且基帧在 obs_ids 内")
    check(c["c1_unrecorded"] == 1, "id 2 没记基帧")
    check(c["c1_base_outside_ids"] == 1, "id 5 记了基帧但不在 obs_ids 里")
    check(c["c2_desc_rows"] == 4, f"含称呼的行应为 4 条，得 {c['c2_desc_rows']}")
    check(c["c2_fresh"] == 1, "id 1 的称呼是首次出现，不算沿用")
    check(c["c2_carried"] == 3, f"沿用应为 3 条（3/7/8），得 {c['c2_carried']}")
    check(c["c2_carried_adjacent"] == 1, "id 8 与上一行同词面：相邻沿用")
    check(c["c2_carried_gap"] == 2, f"id 3/7 相对更早的行沿用：跨行沿用，得 {c['c2_carried_gap']}")
    check(c["c2_cast"] == 1, "id 7 点了名：合规")
    check(c["c2_silent"] == 2, f"静默沿用应为 2 条（3/8），得 {c['c2_silent']}")
    check(c["c2_silent_gap"] == 1, "其中 id 3 是跨行静默沿用（写入器只记上一行，拦不住它）")
    check(c["defect_rows"] == [2, 3, 5, 8], f"C 缺陷行应为 [2,3,5,8]，得 {c['defect_rows']}")

    # 判定人口：--since-msg-id 之后没有行 → 退出 2（不能确立），不是 0
    check(d1_census.main(["--db", str(db), "--since-msg-id", "8"]) == 2,
          "判定人口为空必须退出 2")
    # 判定人口含缺陷 → 退出 1
    check(d1_census.main(["--db", str(db), "--since-msg-id", "0"]) == 1, "有缺陷必须退出 1")
    # 判定人口只含合规行 → 退出 0
    ok = tmp / "ok.db"
    build(ok)
    con2 = sqlite3.connect(str(ok))  # 这里要写，故不用普查的只读连接
    con2.execute("DELETE FROM messages WHERE id IN (2,3,5,6,8)")
    con2.commit()
    con2.close()
    check(d1_census.main(["--db", str(ok), "--since-msg-id", "0"]) == 0,
          "只剩合规行时必须退出 0")
    # 抽样可复现：同种子同结果
    s1 = d1_census.sample(d1_census.connect(str(db)), 3, 3371)
    s2 = d1_census.sample(d1_census.connect(str(db)), 3, 3371)
    check(s1["rows"] == s2["rows"], "同 seed 抽样必须可复现")
    # 库不存在 → 退出 2
    check(d1_census.main(["--db", str(tmp / "nope.db")]) == 2, "库不可读必须退出 2")

    con.close()
    con2.close()
    sqlite3.connect(str(db)).close()
    print(f"D1 census smoke: ALL PASS（{CHECKS} 条断言）")


if __name__ == "__main__":
    main()
