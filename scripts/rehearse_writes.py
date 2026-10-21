"""预演：把冻结快照的 small 正文用**现在的**写入规则重算一遍证据基，写进临时库。

为什么要有它：D1 的 A/C1/C2 是在历史库上数的，那些行是旧规则写的——规则改了，库里
还是旧账。改完写入规则、真跑之前，先回答一个问题：**同样的模型内容、新的规则**，
分子分母会变成什么？本脚本只回答这个，不做别的：

  1. 正文不动：每条 small 行的 content/intent/type 原样搬过来（模型说过的话不重写）；
  2. 证据基重算：帧仍是该行原来的 obs_ids 成员，其后全部交给 `Witness._row_basis`
     ——窗口取引用集合极值、对比句并基帧、通行称呼点名才留；
  3. 落库仍走 `Witness._write_small`（与 process / process_batch 同一段写入），
     所以预演不是第二套规则，只是同一套规则喂了旧内容；
  4. 写完由 `scripts/d1_census.py` 判 A/C1/C2（预演库里每一行都是新规则写的，
     故不带 --since-msg-id，判定人口就是全体）。

快照只读（`mode=ro&immutable=1`），一个字节都不写回；不发任何模型请求。

可核对的边界（都是 inference，来自快照记录不到的字段）：
- `since`：旧行不记模型点名的帧，故一律当作没点名，基帧走 `_switch_base` 的缺省
  （上一条 small 里 ts_wall 最新的那一帧）——这正是新规则的缺省路径；
- `cast`：旧行不记点名，故当作没有点名（快照里 cast 命中数为 0，这条是空的）；
- 本轮图数 n_frames：旧行不记"模型看到几张图"，故取"被引用观察里仍存在且 ref 非空的
  个数"作代理；引用已被驱逐的行因此按 0 张图算（这类行单独计数，见 missing_refs）。

缺陷不改：本脚本若暴露规则缺陷，写进报告，写入规则留给下一个包。

用法：
  python3 scripts/rehearse_writes.py --db ~/backseat-data/snapshots/<UTC 日期>/backseat.db \
      --out /tmp/rehearse-$(git rev-parse --short HEAD).db [--limit 3000]
  python3 scripts/d1_census.py --db /tmp/rehearse-<sha>.db
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from backseat.config import Config
from backseat.store import Store
from backseat.witness import SWITCH_RE, Witness, parse_ids

OBS_COLS = ("id", "ts_wall", "ts_mono", "sensor", "kind", "display", "key",
            "content", "ref", "ref_full", "hash", "bytes", "pinned", "meta")


class NoModel:
    """预演不发模型请求：一句话都不生成，内容全部来自快照已有的行。"""

    kind = "rehearsal"

    def __init__(self) -> None:
        self.model = "rehearsal"

    def chat(self, messages):  # 只会在规则被改坏、预演误发请求时触发
        raise AssertionError("预演不允许发起模型请求：内容只来自快照已有的行")


class NoTrajectory:
    """预演没有调用可记：不落 trajectory，也不落 metrics。"""

    def record(self, *a, **k):
        raise AssertionError("预演没有模型调用，不该有 trajectory 行")


def connect_ro(db: str) -> sqlite3.Connection:
    """只读连接：快照是证据，预演不改它（journal 也不碰）。"""
    con = sqlite3.connect(f"file:{db}?mode=ro&immutable=1", uri=True)
    con.row_factory = sqlite3.Row
    return con


def parse_payload(raw) -> dict:
    try:
        d = json.loads(raw) if raw else {}
    except (ValueError, TypeError):
        return {}
    return d if isinstance(d, dict) else {}


def copy_observations(snap: sqlite3.Connection, out: Store) -> int:
    """观察表整份搬进临时库：窗口与基帧都要在临时库里查得到 id→ts_wall。

    `ref` 原样搬（不复制帧文件）：预演不看像素，只看"这一帧在不在、属于哪一刻"。
    """
    rows = snap.execute(f"SELECT {','.join(OBS_COLS)} FROM observations").fetchall()
    qs = ",".join("?" * len(OBS_COLS))
    out.conn.executemany(
        f"INSERT INTO observations({','.join(OBS_COLS)}) VALUES({qs})",
        [tuple(r[c] for c in OBS_COLS) for r in rows])
    out.conn.commit()
    return len(rows)


def quantile(sorted_vals: list[int], q: float) -> int:
    """最近秩分位：空集返回 0（空就是空，由调用方当缺值说明）。"""
    if not sorted_vals:
        return 0
    pos = round(q * (len(sorted_vals) - 1))
    return sorted_vals[min(len(sorted_vals) - 1, max(0, pos))]


def dist(vals: list[int]) -> dict:
    s = sorted(vals)
    return {"n": len(s), "median": quantile(s, 0.5), "p95": quantile(s, 0.95),
            "p99": quantile(s, 0.99), "max": s[-1] if s else 0, "mean": round(sum(s) / len(s), 3) if s else 0}


def mini_of(row) -> dict:
    """旧行 → 一份 mini：正文/意图/类型原样，payload 里的模型字段原样，cast 用记下的（通常没有）。"""
    payload = parse_payload(row["payload"])
    kind = row["type"] or "diff"
    body = row["content"] or ""
    mini: dict = {"type": kind, "intent": row["intent"] or ""}
    mini["describe" if kind == "anchor" else "diff"] = body
    if payload.get("importance") is not None:
        mini["importance"] = payload["importance"]
    if payload.get("candidate_project"):
        mini["candidate_project"] = payload["candidate_project"]
    if isinstance(payload.get("cast"), list):
        mini["cast"] = payload["cast"]
    return mini


def rehearse(snap: sqlite3.Connection, out: Store, log=None,
             limit: int | None = None) -> dict:
    """把快照的 small 正文按现在的写入规则重算证据基，写进 `out`。返回计数。"""
    walls = {r["id"]: r["ts_wall"]
             for r in snap.execute("SELECT id, ts_wall FROM observations")}
    shot = {r["id"]: r["ref"] is not None
            for r in snap.execute("SELECT id, ref FROM observations")}
    copy_observations(snap, out)

    w = Witness(out, NoModel(), NoTrajectory(), Config(),
                log=log or (lambda *a: None))
    sql = ("SELECT id, ts_start, ts_end, content, intent, type, model, payload, obs_ids "
           "FROM messages WHERE level='small' ORDER BY id")
    rows = snap.execute(sql + (" LIMIT ?" if limit else ""),
                        (limit,) if limit else ()).fetchall()

    st = {"limit": limit or 0, "rows_in": 0, "written": 0, "dropped_empty": 0, "empty_obs_ids": 0,
          "missing_refs": 0, "missing_refs_all": 0, "marker_rows": 0, "switch_base": 0,
          "obs_len_in": [], "obs_len_out": [], "dropped_ids": []}
    for row in rows:
        st["rows_in"] += 1
        ids = parse_ids(row["obs_ids"])
        st["obs_len_in"].append(len(ids))
        if not ids:
            st["empty_obs_ids"] += 1
        missing = [i for i in ids if i not in walls]
        if missing:
            st["missing_refs"] += 1
            if len(missing) == len(ids):
                st["missing_refs_all"] += 1
        # 本轮图数的代理：被引用观察里仍存在、且 ref 非空的个数
        n_frames = sum(1 for i in ids if shot.get(i))
        mini = mini_of(row)
        w.model.model = row["model"] or "rehearsal"  # 话是谁说的就是谁说的
        if SWITCH_RE.search(row["content"] or ""):
            st["marker_rows"] += 1
        content, ids_out, ts_start, ts_end, base, refused, retained = w._row_basis(
            mini, row["content"] or "", ids, row["ts_end"], n_frames)
        if content is None:
            st["dropped_empty"] += 1
            st["dropped_ids"].append(int(row["id"]))
            continue
        payload = w._payload(mini, base, refused, retained)
        w._write_small(mini, content, ids_out, ts_start, ts_end, payload)
        st["written"] += 1
        st["obs_len_out"].append(len(ids_out))
        if base:
            st["switch_base"] += 1
    st["obs_in"] = dist(st.pop("obs_len_in"))
    st["obs_out"] = dist(st.pop("obs_len_out"))
    st["refused_total"] = int(w.stats.get("epithet_refused", 0))
    return st


def report(st: dict, db: str, out: str) -> None:
    print(f"snapshot={db} out={out} limit={st['limit'] or '全员'}")
    print(f"rows_in={st['rows_in']} written={st['written']} dropped_empty={st['dropped_empty']}")
    print(f"empty_obs_ids_rows={st['empty_obs_ids']} missing_ref_rows={st['missing_refs']} "
          f"(all_cited_evicted={st['missing_refs_all']})")
    i, o = st["obs_in"], st["obs_out"]
    print(f"obs_ids_len_in  n={i['n']} median={i['median']} p95={i['p95']} p99={i['p99']} max={i['max']}")
    print(f"obs_ids_len_out n={o['n']} median={o['median']} p95={o['p95']} p99={o['p99']} max={o['max']}")
    denom = st["written"] or 1
    print(f"switch_base rows={st['switch_base']} / marker_rows={st['marker_rows']} "
          f"/ written={st['written']}  "
          f"rate(marker)={st['switch_base'] / (st['marker_rows'] or 1):.4f} "
          f"rate(written)={st['switch_base'] / denom:.4f}")
    print(f"epithet_refused_forms={st['refused_total']}")
    if st["dropped_ids"]:
        print(f"dropped_ids={st['dropped_ids'][:20]}"
              f"{' …' if len(st['dropped_ids']) > 20 else ''}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="写入规则预演（只读快照，写临时库）")
    ap.add_argument("--db", required=True, help="冻结快照路径（只读打开）")
    ap.add_argument("--out", required=True, help="预演库路径（会被重建）")
    ap.add_argument("--limit", type=int, default=None,
                    help="只重放前 N 条 small 行（切片；默认全体）：报告里的数字要能从快照一条命令复现")
    ap.add_argument("--verbose", action="store_true", help="把规则拒绝逐条打到 stderr")
    args = ap.parse_args(argv)

    out_path = Path(args.out)
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(out_path) + suffix)
        if p.exists():
            p.unlink()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        snap = connect_ro(args.db)
        snap.execute("SELECT count(*) FROM messages").fetchone()
    except sqlite3.Error as e:
        print(f"库不可读：{args.db}: {e}", file=sys.stderr)
        return 2

    out = Store(out_path)
    st = rehearse(snap, out, log=(print if args.verbose else (lambda *a: None)),
                  limit=args.limit)
    out.close()
    report(st, args.db, str(out_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
