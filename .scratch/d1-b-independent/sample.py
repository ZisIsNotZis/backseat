#!/usr/bin/env python3
"""D1 B independent sample + resolution dump.

Method: messages of level='small' ordered by id; the 50 rows are
`random.Random(SEED).sample(range(n_small), 50)` over that id-ordered list,
sorted by id for presentation. SEED = 3371 (0xD1B), fixed before any row was
inspected. Population n_small is derived from the database at run time.

Read-only: the snapshot is opened with mode=ro&immutable=1.
"""
import json, os, random, sqlite3, sys

DB = os.path.expanduser("~/backseat-data/snapshots/2026-10-19/backseat.db")
SEED = 3371
N = 50

con = sqlite3.connect(f"file:{DB}?mode=ro&immutable=1", uri=True)
con.row_factory = sqlite3.Row

ids = [r[0] for r in con.execute("select id from messages where level='small' order by id")]
n = len(ids)
picked = sorted(random.Random(SEED).sample(range(n), N))
rows = [ids[i] for i in picked]

out = []
for idx, mid in zip(picked, rows):
    m = con.execute("select * from messages where id=?", (mid,)).fetchone()
    raw = json.loads(m["obs_ids"] or "[]")
    refs = []
    for oid in raw:
        o = con.execute("select id,ts_wall,kind,key,content,ref,meta from observations where id=?", (oid,)).fetchone()
        if o is None:
            refs.append({"obs_id": oid, "exists": False})
        else:
            refs.append({
                "obs_id": oid, "exists": True, "ts_wall": o["ts_wall"],
                "in_window": m["ts_start"] <= o["ts_wall"] <= m["ts_end"],
                "delta_to_start": round(o["ts_wall"] - m["ts_start"], 3),
                "delta_to_end": round(o["ts_wall"] - m["ts_end"], 3),
                "kind": o["kind"], "key": o["key"], "content": o["content"],
                "ref": o["ref"], "meta": o["meta"],
                "file_exists": bool(o["ref"]) and os.path.exists(o["ref"]),
            })
    out.append({
        "rank_in_pop": idx, "msg_id": mid,
        "ts_start": m["ts_start"], "ts_end": m["ts_end"],
        "inverted_window": m["ts_start"] > m["ts_end"],
        "type": m["type"], "source": m["source"],
        "intent": m["intent"], "content": m["content"], "payload": m["payload"],
        "obs_ids": raw, "refs": refs,
    })

json.dump({"db": DB, "seed": SEED, "population": n, "picked_indices": picked, "rows": out},
          sys.stdout, ensure_ascii=False, indent=1)
