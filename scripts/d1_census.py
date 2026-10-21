"""D1 普查：在冻结快照上数 D1 的各分项，只读，不改库，判据由一条命令复算。

为什么要有它：D1 的分子分母原本散在两处——A 是手工跑出来的数字，B 是一次独立评审的
50 行表格，写入规则（切换句基帧、通行称呼）只有测试没有计数。重算一次要靠人重读报告，
而报告会腐。本脚本把**可机械判定**的部分变成命令：

  A     每条 small 行的每个 obs_ids 成员：id 存在，且该观察 ts_wall 落在本行 [ts_start, ts_end]
  C1    内容里有对比/过渡句的行：payload.switch_base 非空，且它 ⊆ 本行 obs_ids（依据在行内）
  C2    内容里有人物通行称呼的行：payload.cast 记明保留的称呼（没有记明也没有
        payload.epithet_refused 的就是静默沿用——那是缺陷，不是零）

判定人口是 `--since-msg-id N` 之后写入的 small 行：`messages.id` 单调，历史行不会变，
所以历史行（id ≤ N）只作**已知降级**计数，不进判定。判定人口为空时退出 2，
因为"没有行可判"不是"通过"（缺值记不能确立，不记 0）。

`--sample K --seed S` 复现独立评审的抽样（默认 50 / 3371），并把两套词面并排打出：
写入用的 `SWITCH_RE`（witness 里的唯一事实源）与只用于测召回的 `DIAGNOSTIC_RE`。
两个数的差就是"标记表漏掉的对比句"——它用来看宽度决策，不参与写入。

用法：
  python3 scripts/d1_census.py --db ~/backseat-data/snapshots/<UTC 日期>/backseat.db
  python3 scripts/d1_census.py --db … --since-msg-id 24551 --json .tmp/d1.json
  python3 scripts/d1_census.py --db … --sample 50 --seed 3371

退出码：0 判定人口无缺陷；1 判定人口有缺陷；2 判定人口为空 / 库不可读 / 参数误用。
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from backseat.witness import (  # 写入判据与称呼名词尾的唯一事实源
    SWITCH_RE,
    epithet_forms,
)

# 只用于测召回：比写入用的 SWITCH_RE 宽，覆盖评审在 50 行里数到、而 SWITCH_RE 漏掉的词面。
# 它不写库、不判定，只把"标记表漏了多少"变成一个数。
DIAGNOSTIC_RE = re.compile(
    r"从[^，。；,;\s]{1,24}?(?:切|转|变)|由[^，。；,;\s]{1,24}?(?:切|转|变)"
    r"|切至|切换为|切为|画面切|镜头切|相较上一帧|相较前帧|相较|仍[为是]|仍是|仍保持|相对上一")


def connect(db: str) -> sqlite3.Connection:
    """只读连接：快照是证据，普查不改它（journal 也不碰）。"""
    con = sqlite3.connect(f"file:{db}?mode=ro&immutable=1", uri=True)
    con.row_factory = sqlite3.Row
    return con


def load(con: sqlite3.Connection):
    """small 行（按 id）与 observations 的 id→ts_wall。"""
    rows = con.execute(
        "SELECT id, ts_start, ts_end, content, payload, obs_ids FROM messages "
        "WHERE level='small' ORDER BY id").fetchall()
    walls = {r["id"]: r["ts_wall"] for r in con.execute("SELECT id, ts_wall FROM observations")}
    return rows, walls


def parse_ids(raw) -> list[int]:
    try:
        return [int(x) for x in json.loads(raw)] if raw else []
    except (ValueError, TypeError):
        return []


def parse_payload(raw) -> dict:
    try:
        d = json.loads(raw) if raw else {}
    except (ValueError, TypeError):
        return {}
    return d if isinstance(d, dict) else {}


def census_a(rows, walls) -> dict:
    """A：id 存在且在自窗内。返回计数 + 缺陷行 id。"""
    out = {"rows": 0, "refs": 0, "missing_ids": 0, "refs_outside": 0,
           "rows_with_outside": 0, "inverted_windows": 0, "defect_rows": []}
    for r in rows:
        out["rows"] += 1
        ids = parse_ids(r["obs_ids"])
        if r["ts_start"] > r["ts_end"]:
            out["inverted_windows"] += 1
        bad = 0
        for i in ids:
            out["refs"] += 1
            w = walls.get(i)
            if w is None:
                out["missing_ids"] += 1
                bad += 1
            elif not (r["ts_start"] - 1e-9 <= w <= r["ts_end"] + 1e-9):
                out["refs_outside"] += 1
                bad += 1
        if bad:
            out["rows_with_outside"] += 1
            out["defect_rows"].append(int(r["id"]))
        elif r["ts_start"] > r["ts_end"]:
            out["defect_rows"].append(int(r["id"]))
    return out


def census_c(rows) -> dict:
    """C1/C2：对比句的基帧、通行称呼，是否在本行 payload 里记明（依据在行内）。

    C2 的分母是**被沿用的**称呼，不是所有称呼：第一次出现的称呼本来就只可能来自本帧，
    要求它点名等于要求模型说废话。区别在 id 序上早于本行的行里是否已经用过这个词面。
    沿用的远近要分开数：上一行用过（相邻沿用）与更早用过（跨行沿用）——写入器的
    `state["epithets"]` 只记上一行，所以两者不是同一个机制能拦住的。
    """
    out = {"c1_rows": 0, "c1_recorded": 0, "c1_unrecorded": 0, "c1_base_outside_ids": 0,
           "c2_desc_rows": 0, "c2_fresh": 0, "c2_carried": 0, "c2_carried_adjacent": 0,
           "c2_carried_gap": 0, "c2_cast": 0, "c2_refused": 0, "c2_silent": 0,
           "c2_silent_gap": 0, "defect_rows": []}
    seen: set[str] = set()      # 到上一行为止用过的词面
    prev: set[str] = set()      # 上一行的称呼集合
    for r in rows:
        content = r["content"] or ""
        payload = parse_payload(r["payload"])
        ids = set(parse_ids(r["obs_ids"]))
        if SWITCH_RE.search(content):
            out["c1_rows"] += 1
            base = payload.get("switch_base")
            base = [int(x) for x in base] if isinstance(base, list) else []
            if not base:
                out["c1_unrecorded"] += 1
                out["defect_rows"].append(int(r["id"]))
            elif not set(base) <= ids:
                out["c1_base_outside_ids"] += 1
                out["defect_rows"].append(int(r["id"]))
            else:
                out["c1_recorded"] += 1
        forms = set(epithet_forms(content))
        if forms:
            out["c2_desc_rows"] += 1
            carried = forms & seen
            if not carried:
                out["c2_fresh"] += 1
            else:
                out["c2_carried"] += 1
                if carried & prev:
                    out["c2_carried_adjacent"] += 1
                if carried - prev:
                    out["c2_carried_gap"] += 1
                if payload.get("cast"):
                    out["c2_cast"] += 1
                elif payload.get("epithet_refused"):
                    out["c2_refused"] += 1
                else:
                    out["c2_silent"] += 1
                    if carried - prev:
                        out["c2_silent_gap"] += 1
                    out["defect_rows"].append(int(r["id"]))
        seen |= forms
        prev = forms
    out["defect_rows"] = sorted(set(out["defect_rows"]))
    return out


def report(rows, walls, since: int) -> dict:
    judged = [r for r in rows if int(r["id"]) > since]
    historic = [r for r in rows if int(r["id"]) <= since]
    return {
        "max_small_id": max((int(r["id"]) for r in rows), default=0),
        "since_msg_id": since,
        "judged_rows": len(judged),
        "historic_rows": len(historic),
        "judged": {"A": census_a(judged, walls), "C": census_c(judged)},
        "historic_known_degraded": {"A": census_a(historic, walls), "C": census_c(historic)},
    }


def sample(con, k: int, seed: int) -> dict:
    """复现评审的抽样：id 升序总体 + random.Random(seed).sample(range(n), k)。"""
    pop = [int(r["id"]) for r in con.execute(
        "SELECT id FROM messages WHERE level='small' ORDER BY id")]
    if not pop:
        return {"picked": [], "note": "population empty"}
    idx = sorted(random.Random(seed).sample(range(len(pop)), min(k, len(pop))))
    picked = [pop[i] for i in idx]
    qs = ",".join("?" * len(picked))
    rows = {int(r["id"]): r for r in con.execute(
        f"SELECT id, ts_start, ts_end, content, payload, obs_ids FROM messages WHERE id IN ({qs})",
        picked)}
    fam = {"switch": 0, "diagnostic_only": 0, "none": 0}
    detail = []
    for n, mid in enumerate(picked, 1):
        r = rows[mid]
        content = r["content"] or ""
        has_switch, has_diag = bool(SWITCH_RE.search(content)), bool(DIAGNOSTIC_RE.search(content))
        if has_switch:
            fam["switch"] += 1
        elif has_diag:
            fam["diagnostic_only"] += 1
        else:
            fam["none"] += 1
        detail.append({"n": n, "msg_id": mid, "switch_re": has_switch,
                       "diagnostic_re": has_diag,
                       "obs_ids": len(parse_ids(r["obs_ids"])),
                       "content_head": content[:60]})
    return {"seed": seed, "k": k, "families": fam, "rows": detail}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="D1 普查（只读）")
    ap.add_argument("--db", required=True, help="冻结快照路径（只读打开）")
    ap.add_argument("--since-msg-id", type=int, default=0,
                    help="判定人口：id 大于它的 small 行（历史行只作已知降级计数）")
    ap.add_argument("--json", help="把整份报告写到该路径")
    ap.add_argument("--sample", type=int, default=0, help="另抽 K 行看词面（复现评审抽样）")
    ap.add_argument("--seed", type=int, default=3371, help="抽样种子（默认 3371，评审用的那个）")
    args = ap.parse_args(argv)

    try:
        con = connect(args.db)
        rows, walls = load(con)
    except sqlite3.Error as e:
        print(f"库不可读：{args.db}: {e}", file=sys.stderr)
        return 2

    rep = report(rows, walls, args.since_msg_id)
    J, H = rep["judged"], rep["historic_known_degraded"]
    print(f"snapshot={args.db} small_rows={len(rows)} max_small_id={rep['max_small_id']}")
    print(f"judged_rows={rep['judged_rows']} (id>{args.since_msg_id})  "
          f"historic_rows={rep['historic_rows']}（已知降级，不进判定）")
    print(f"A  refs={J['A']['refs']} missing={J['A']['missing_ids']} "
          f"outside={J['A']['refs_outside']} rows_outside={J['A']['rows_with_outside']} "
          f"inverted={J['A']['inverted_windows']}")
    print(f"C1 marker_rows={J['C']['c1_rows']} recorded={J['C']['c1_recorded']} "
          f"unrecorded={J['C']['c1_unrecorded']} base_outside_ids={J['C']['c1_base_outside_ids']}")
    print(f"C2 desc_rows={J['C']['c2_desc_rows']} fresh={J['C']['c2_fresh']} "
          f"carried={J['C']['c2_carried']} (adjacent={J['C']['c2_carried_adjacent']} "
          f"gap={J['C']['c2_carried_gap']}) cast={J['C']['c2_cast']} "
          f"refused={J['C']['c2_refused']} silent={J['C']['c2_silent']} "
          f"silent_gap={J['C']['c2_silent_gap']}")
    print(f"历史  A rows_outside={H['A']['rows_with_outside']} refs_outside={H['A']['refs_outside']}"
          f"/{H['A']['refs']} inverted={H['A']['inverted_windows']}  "
          f"C1 unrecorded={H['C']['c1_unrecorded']}/{H['C']['c1_rows']}  "
          f"C2 silent={H['C']['c2_silent']}/{H['C']['c2_carried']}")

    defects = sorted(set(J["A"]["defect_rows"]) | set(J["C"]["defect_rows"]))
    if args.sample:
        s = sample(con, args.sample, args.seed)
        rep["sample"] = s
        print(f"sample k={s['k']} seed={s['seed']} families={s['families']}"
              "  （switch_re=写入判据命中；diagnostic_only=只有宽词面命中，即标记表漏掉的）")
        for d in s["rows"]:
            print(f"  {d['n']:2d} msg{d['msg_id']:6d} sw={int(d['switch_re'])} "
                  f"diag={int(d['diagnostic_re'])} obs={d['obs_ids']:2d} {d['content_head']}")

    if args.json:
        rep["defect_rows"] = defects
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(rep, ensure_ascii=False, indent=1), "utf-8")
        print(f"json={args.json} defect_rows={len(defects)}")

    if rep["judged_rows"] == 0:
        print("判定人口为空：不能确立（不是通过）")
        return 2
    if defects:
        print(f"判定人口缺陷行 {len(defects)}：{defects[:20]}{' …' if len(defects) > 20 else ''}")
        return 1
    print("判定人口无缺陷")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
