"""回填称呼册：把库里已有 small 行的通行称呼词面全写进 `epithets` 表。

为什么要有它：C2 的“被沿用的称呼”是**跨全库**比的——写入器只认册子里的词面，
而历史行写下时册子可能还不存在（旧设计只记上一行、封顶 200）。不回填，修复后第一次
重写历史里的名字就不在册里，于是静默沿用。回填把这批词面一次性搬进册，之后每一行
的比对就是全库口径。

口径与写入端同一处：词面由 `backseat.witness.epithet_forms`（名义名前 2–6 字后缀变体，
去“的”）生成，表结构用 `backseat.witness.EPITHET_SCHEMA`，不另写一套。

幂等：只插不删，`ON CONFLICT(form) DO UPDATE SET last_wall=MAX(last_wall, excluded)`，
跑两次库里内容一样（第二次不新增词面，只把 last_wall 推到历史的真实最晚）。

用法：
  python3 scripts/backfill_epithets.py --db ~/backseat-data/backseat.db
  python3 scripts/backfill_epithets.py --db /tmp/slice.db   # 预演切片同理

退出码：0 回填完成；2 库不可读。
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from backseat.witness import EPITHET_SCHEMA, EPITHET_TABLE, epithet_forms


def backfill(conn: sqlite3.Connection, batch: int = 500) -> dict:
    """把每个 small 行的词面写进册。返回计数（行、词面、新增）。"""
    conn.row_factory = sqlite3.Row
    conn.execute(EPITHET_SCHEMA)
    before = conn.execute(f"SELECT count(*) AS c FROM {EPITHET_TABLE}").fetchone()["c"]
    rows = conn.execute(
        "SELECT content, ts_start, ts_end FROM messages WHERE level='small' ORDER BY id"
    ).fetchall()
    pending, seen_rows = [], 0
    for r in rows:
        forms = epithet_forms(r["content"] or "")
        if not forms:
            continue
        seen_rows += 1
        wall = r["ts_end"] if r["ts_end"] is not None else (r["ts_start"] or 0.0)
        pending.extend((f, wall, wall) for f in forms)
        if len(pending) >= batch:
            _flush(conn, pending)
            pending = []
    if pending:
        _flush(conn, pending)
    conn.commit()
    after = conn.execute(f"SELECT count(*) AS c FROM {EPITHET_TABLE}").fetchone()["c"]
    return {"small_rows": len(rows), "rows_with_forms": seen_rows,
            "forms_before": before, "forms_after": after, "forms_added": after - before}


def _flush(conn: sqlite3.Connection, pending: list[tuple[str, float, float]]) -> None:
    conn.executemany(
        f"INSERT INTO {EPITHET_TABLE}(form,first_wall,last_wall) VALUES(?,?,?) "
        "ON CONFLICT(form) DO UPDATE SET last_wall=MAX(last_wall, excluded.last_wall)",
        pending)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="回填称呼册（写库，幂等）")
    ap.add_argument("--db", required=True, help="要回填的库（就地写 epithets 表）")
    args = ap.parse_args(argv)
    try:
        conn = sqlite3.connect(args.db)
        st = backfill(conn)
        conn.close()
    except sqlite3.Error as e:
        print(f"库不可读：{args.db}: {e}", file=sys.stderr)
        return 2
    print(f"db={args.db} small_rows={st['small_rows']} rows_with_forms={st['rows_with_forms']} "
          f"forms={st['forms_after']} added={st['forms_added']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
