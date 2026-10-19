"""L1 评测尺子执行器：只读打开冻结库 → 生成题集 → 跑三条基线 → 出中文 Markdown 报告。

只测尺子，不测理解：本包没有预测模型、不调用 LLM、不联网。基线全是现成口径
（常数猜测、原始表直查、现行 --stats），用来标定“尺子能不能把不同方法分开”。
判对一律走 D2 的 `score`（时间题 ±60s 容差，其余规范化后相等）。

只读保证：`file:...?mode=ro` 打开 + `PRAGMA query_only=1`，写入被 SQLite 拒绝。
`PRAGMA data_version` 只是辅助观察——它只反映**其它连接**的提交，同一连接自己的写入
不会改变它，所以它不是只读的证据；比对不一致时报告作废且不打印。

用法：
  python3 evals/run_eval.py --db ~/backseat-data/backseat.db --n 50 [--seed 0] [--json out.json]

退出码：0 成功；2 库不可用（不存在/缺表/打开失败/评测期间被写入）；
       4 空池（生成 0 道题）；5 尺子自检失败（复算或引用判定未全过）；
       6 参数误用（`--n` 小于 1）。
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from evals.questions import (  # noqa: E402
    CONTENT_PREFIX, KINDS, UNAVAILABLE_FIELDS, generate_questions, score, utc,
    verify_question)

BASELINES = ("B0 常数（多数类）", "B1 原始表直查", "B2 --stats 口径")
_KEY_RE = re.compile(r"phash:[0-9a-f]{16}")
_ID_RE = re.compile(r"id=(\d+)")


def open_ro(path: Path | str) -> sqlite3.Connection:
    """只读打开：URI mode=ro + query_only，任何写入都会抛错。"""
    p = Path(path).expanduser().resolve()
    conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=1")
    return conn


def data_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA data_version").fetchone()[0])


def load_stats(conn: sqlite3.Connection) -> dict:
    """现行 `python3 -m backseat --stats` 会打印的总量口径。"""
    m = conn.execute(
        "SELECT count(*) c, coalesce(sum(tokens_in),0) tin, coalesce(sum(tokens_out),0) tout, "
        "coalesce(sum(cached_tokens),0) tc FROM metrics").fetchone()
    obs = conn.execute(
        "SELECT count(*) c, coalesce(sum(bytes),0) b FROM observations").fetchone()
    by_level = {r["level"]: r["c"] for r in conn.execute(
        "SELECT level, count(*) c FROM messages GROUP BY level")}
    distinct = conn.execute(
        "SELECT count(DISTINCT key) FROM observations WHERE key IS NOT NULL").fetchone()[0]
    return {"calls": m["c"], "tokens_in": m["tin"], "tokens_out": m["tout"],
            "cached": m["tc"], "observations": obs["c"], "bytes": obs["b"],
            "messages_by_level": by_level, "small": by_level.get("small", 0),
            "distinct_keys": int(distinct)}


def predict_b0(questions: list[dict]) -> dict[str, str]:
    """B0：每题型取出现最多的答案（多数类）。借用了答案分布本身，是常数猜测的作弊上界。"""
    modal: dict[str, str] = {}
    for kind in KINDS:
        answers = [q["answer"] for q in questions if q["kind"] == kind]
        if answers:
            modal[kind] = Counter(answers).most_common(1)[0][0]
    return {q["id"]: modal.get(q["kind"], "") for q in questions}


def predict_b1(conn: sqlite3.Connection, q: dict) -> str:
    """B1：把题面当关键词在原始表里直查——phash 取第一条命中、`id=` 直取该行、
    聚合类题不给窗口直接返回全表总量。不做时间窗与聚合推理。

    `content` 分支当前不可达（content 题题干不再给 `messages.id`），但它是 B1 的定义
    而不是脚手架：只要题干出现 `id=`，B1 就该直取那一行，所以不删。
    """
    text = q["question"]
    if q["kind"] == "time":
        hit = _KEY_RE.search(text)
        if not hit:
            return ""
        row = conn.execute(
            "SELECT substr(datetime(ts_wall,'unixepoch'),1,19) FROM observations "
            "WHERE key=? ORDER BY ts_wall LIMIT 1", (hit.group(0),)).fetchone()
        return row[0] if row else ""
    if q["kind"] == "count":
        if "不同帧 key" in text:
            return str(conn.execute(
                "SELECT count(DISTINCT key) FROM observations "
                "WHERE key IS NOT NULL").fetchone()[0])
        return str(conn.execute(
            "SELECT count(*) FROM messages WHERE level='small'").fetchone()[0])
    hit = _ID_RE.search(text)
    if not hit:
        return ""
    mid = int(hit.group(1))
    if q["kind"] == "content":
        row = conn.execute(
            f"SELECT substr(content,1,{CONTENT_PREFIX}) FROM messages WHERE id=?",
            (mid,)).fetchone()
        return row[0] if row and row[0] is not None else ""
    row = conn.execute(
        "SELECT o.key FROM messages m JOIN json_each(m.obs_ids) j "
        "JOIN observations o ON o.id=j.value WHERE m.id=?", (mid,)).fetchone()
    return row[0] if row else ""


def predict_b2(stats: dict, q: dict) -> str:
    """B2：现行 --stats 口径只有总量，答不出任何单题——窗口类题给总量，其余明确说没有此口径。"""
    if q["kind"] != "count":
        return "（--stats 无此口径）"
    if "不同帧 key" in q["question"]:
        return str(stats["distinct_keys"])
    return str(stats["small"])


def evaluate(conn: sqlite3.Connection, questions: list[dict], stats: dict) -> dict:
    """逐题算三条基线的预测（按 D2 的 score 判对），以及尺子自检（复算 + 逐条引用）。

    引用指标按 D2 分两个：`引用正确率`（逐条引用，分母=引用总条数）与
    `证据存在率`（按题，分母=题数）。两者不互相顶替。
    """
    preds = {b: {} for b in BASELINES}
    b0 = predict_b0(questions)

    def bucket() -> dict:
        return {"n": 0, "recalc_ok": 0, "evidence_passed": 0, "evidence_total": 0,
                "evidence_exists": 0, **{name: 0 for name in BASELINES}}

    per_kind = {kind: bucket() for kind in KINDS}
    overall = bucket()
    for q in questions:
        check = verify_question(conn, q)
        preds[BASELINES[0]][q["id"]] = b0[q["id"]]
        preds[BASELINES[1]][q["id"]] = predict_b1(conn, q)
        preds[BASELINES[2]][q["id"]] = predict_b2(stats, q)
        for b in (per_kind[q["kind"]], overall):
            b["n"] += 1
            b["recalc_ok"] += int(check["recalc_ok"])
            b["evidence_passed"] += check["evidence_passed"]
            b["evidence_total"] += check["evidence_total"]
            b["evidence_exists"] += int(check["evidence_exists"])
            for name in BASELINES:
                b[name] += int(score(q, preds[name][q["id"]]))
    semantic = [q for q in questions if q["kind"] != "cite"]
    b1_subset = {"n": len(semantic),
                 "hit": sum(int(score(q, preds[BASELINES[1]][q["id"]])) for q in semantic),
                 "excluded_kind": "cite"}
    return {"per_kind": per_kind, "overall": overall, "predictions": preds,
            "b1_semantic_subset": b1_subset}


def _pct(k: int, n: int) -> str:
    return f"{k / n * 100:.1f}%" if n else "—"


def _frac(k: int, n: int) -> str:
    return f"{k}/{n}（{_pct(k, n)}）" if n else "—"


def _short(text: str, limit: int = 44) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit] + "…"


def unavailable_report(conn: sqlite3.Connection) -> list[dict]:
    """把“缺字段”逐条变成可核的数：探针查询失败也算一条诚实的观察。"""
    out = []
    for item in UNAVAILABLE_FIELDS:
        try:
            probe = int(conn.execute(item["probe_sql"]).fetchone()[0])
        except sqlite3.Error as e:
            probe = f"探针失败：{e}"
        out.append({**item, "probe": probe})
    return out


def render_markdown(report: dict) -> str:
    qs, ev, meta = report["questions"], report["evaluation"], report["meta"]
    dist = {kind: sum(1 for q in qs if q["kind"] == kind) for kind in KINDS}
    total = ev["overall"]
    ro = meta["readonly"]
    dv_state = "一致" if ro["consistent"] else "不一致"
    lines = [
        "# L1 评测尺子报告（WP-EVAL-01）",
        "",
        f"- 库：`{meta['db']}`",
        "- 只读保证：`file:...?mode=ro` 打开 + `PRAGMA query_only=1`，写入会被 SQLite 拒绝；"
        f"`PRAGMA data_version` {ro['before']} → {ro['after']}（{dv_state}）——它只反映**其它连接**的"
        "提交，同一连接自己的写入不会改变它，所以它不是只读的独立证据",
        f"- 种子：{meta['seed']}　请求题量：{meta['requested']}　实际题量：{len(qs)}",
        f"- 题型分布：" + " / ".join(f"{k} {dist[k]}" for k in KINDS),
        f"- `check_sql` 复算：{_frac(total['recalc_ok'], total['n'])} 一致",
        f"- 引用正确率（D2 逐条）：{_frac(total['evidence_passed'], total['evidence_total'])}"
        "（分母=引用总条数；单条通过＝被引单元真实持有声明值、该题 `check_sql` 复算回答案、"
        "且该引用的时间落在答案所述区间）",
        f"- 证据存在率（按题）：{_frac(total['evidence_exists'], total['n'])}"
        "（分母=题数；只判“证据单元在库里存在”，不判时间区间也不判复算）",
        "- 时间口径：全部为 UTC（与 SQLite `datetime(ts,'unixepoch')` 一致）；"
        "时间题判对允许 ±60s 容差，其余题型规范化（去首尾空白、统一小写、压缩空白）后相等",
        "",
        "## 按题型分组的准确率与基线分数",
        "",
        "| 题型 | 题量 | 候选池 | check_sql 复算 | 引用正确率（D2） | 证据存在率 | "
        + " | ".join(BASELINES) + " |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for kind in KINDS:
        b = ev["per_kind"][kind]
        lines.append(
            f"| {kind} | {b['n']} | {report['pool_sizes'][kind]} | "
            f"{_frac(b['recalc_ok'], b['n'])} | "
            f"{_frac(b['evidence_passed'], b['evidence_total'])} | "
            f"{_frac(b['evidence_exists'], b['n'])} | "
            + " | ".join(_pct(b[name], b["n"]) for name in BASELINES) + " |")
    lines.append(
        f"| **合计** | {total['n']} | {sum(report['pool_sizes'].values())} | "
        f"{_frac(total['recalc_ok'], total['n'])} | "
        f"{_frac(total['evidence_passed'], total['evidence_total'])} | "
        f"{_frac(total['evidence_exists'], total['n'])} | "
        + " | ".join(_pct(total[name], total["n"]) for name in BASELINES) + " |")
    subset = ev["b1_semantic_subset"]
    cite_n = dist["cite"]
    lines += [
        "",
        "## 基线口径（B1 必须成对看，别只读一个数）",
        "",
        f"- **B1 全体**（{total['n']} 题，含 cite）：{_frac(total[BASELINES[1]], total['n'])}。"
        f"口径：全部题上的查表下界；cite 题（{cite_n} 题）的答案行由题干主键直接给出，"
        "任何能读库的方法在这类上必然 100%，所以这个数被结构性得分抬高，**不能当能力基线**。",
        f"- **B1 语义子集（剔除 {subset['excluded_kind']} 直取题）**"
        f"（{subset['n']} 题）：{_frac(subset['hit'], subset['n'])}。"
        "口径：同一 B1 定义，只去掉 cite 题；这才是“题干里没有可直接查的主键”的部分的实测下界。"
        "（content 题虽不再给主键，但题干给了（秒, type），若基线改用该键直查同样必得 100%；"
        "当前 B1 只跟 `id=` 与 `phash:`，所以 content 计 0——这是基线定义的下界，不是题型难度。）",
        f"- **{BASELINES[0]}**：{_frac(total[BASELINES[0]], total['n'])}。"
        "口径：每题型取该题型里出现最多的答案，借用了答案分布本身，是“常数猜测最好能得多少分”"
        "的作弊上界。因为生成期对同题型答案去重，任何题型的多数类最多只能命中 1 题，"
        "所以 B0 的低分是去重结构逼出来的，不代表难度。",
        f"- **{BASELINES[2]}**：{_frac(total[BASELINES[2]], total['n'])}。"
        "口径：只用现行 `python3 -m backseat --stats` 会打印的总量口径"
        f"（观察={meta['stats']['observations']}、small 消息={meta['stats']['small']}、"
        f"不同 phash={meta['stats']['distinct_keys']}），答不出任何单题。",
        "",
        "## 样例题（每题型的首题）",
        "",
    ]
    for kind in KINDS:
        first = next((q for q in qs if q["kind"] == kind), None)
        if first:
            lines += [
                f"- **{first['id']}** 题面：{_short(first['question'], 120)}",
                f"  - 答案：`{_short(first['answer'], 60)}`",
                f"  - check_sql：`{first['check_sql']}`",
            ]
    lines += [
        "",
        "## 生成期丢弃（不唯一 / 复算不一致）",
        "",
        "| 题型 | 原因 | 条数 |",
        "|---|---|---|",
    ]
    dropped = report["dropped"]
    # 同一 (题型, 原因) 的多条记录先求和再出行：逐条打印会把 count: 1 打成 N 行同一原因。
    by_reason: dict[tuple[str, str], int] = {}
    for d in dropped:
        key = (d["kind"], d["reason"])
        by_reason[key] = by_reason.get(key, 0) + d["count"]
    for (kind, reason), count in by_reason.items():
        lines.append(f"| {kind} | {reason} | {count} |")
    if not by_reason:
        lines.append("| — | 无 | 0 |")
    lines += [
        "",
        f"丢弃合计：{sum(d['count'] for d in dropped)} 道候选。",
        "",
        "## 因缺字段而无法生成的题型（E5）",
        "",
        "| 题型 | 缺失字段 | 后果 | 探针计数 |",
        "|---|---|---|---|",
    ]
    for u in report["unavailable"]:
        lines.append(f"| {u['kind']} | {u['missing']} | {u['effect']} | {u['probe']} |")
    lines += [
        "",
        "## 口径与限制",
        "",
        "- 本包没有预测模型、不调用 LLM、不联网；三条基线都是现成口径，只用来标定尺子。",
        "- 引用正确率（D2 逐条）与证据存在率（按题）是两个不同的数：前者判每条引用的"
        "复算与时间区间，后者只判证据单元在库里存在；不要用一个顶替另一个。",
        "- 引用正确率衡量尺子自身，不衡量任何理解能力。它的 100% 是生成期过滤的"
        "必然结果（复算不一致的候选在进题集前就被丢弃、引用只取窗口内的真实行），"
        "不是度量出的能力；但它能失败——引一条落在所述区间外的行就会判失败"
        "（`tests/test_evals.py` 有这条证伪断言）；任何一条引用判失败，运行就以退出码 5 结束。",
        "- `count` 题的答案唯一性来自半开窗口的闭式聚合（恒返回单行单值）；题面写明含左端、"
        "不含右端。端点与 `check_sql` 同源于原始浮点，题干用微秒写法表示，舍入 ≤0.5µs；"
        "答案与引用区间用原始浮点，不受该舍入影响。引用的证据只取**落在窗口内**的"
        "首/末真实行，不引窗口外那一行。",
        "- 库是活库（采集守护进程可能写入）；真正的只读保证是 `mode=ro` + `PRAGMA query_only`，"
        "`data_version` 只是辅助观察。",
        "- `content` 题用（ts_start, type, content 的 sha256 前 12 位）定位，题干不出现"
        f"`messages.id`；答案取 `content` 前 {CONTENT_PREFIX} 个字符，生成期已证明该前缀"
        "在 small 消息里只出现一次。",
        "- `cite` 题的题干仍写 `messages.id`：它测的就是回指，允许直取；这也是 `B1 全体` 与"
        "`B1 语义子集` 必须分开报的原因。",
        "",
        "> 本报告只测尺子，不测理解。",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="evals.run_eval", description="L1 评测尺子（只测尺子，不测理解）")
    ap.add_argument("--db", type=Path, default=Path("~/backseat-data/backseat.db"),
                    help="冻结历史库路径（只读打开）")
    ap.add_argument("--n", type=int, default=50, help="题量（必须 ≥1）")
    ap.add_argument("--seed", type=int, default=0, help="抽样种子")
    ap.add_argument("--json", type=Path, default=None, metavar="PATH",
                    help="可选：把题集与基线结果写成 JSON 报告")
    args = ap.parse_args(argv)

    if args.n < 1:  # 参数误用，不是库的毛病：不与退出码 2/4 混在一起报
        print(f"参数 --n 必须 ≥1（收到 {args.n}）", file=sys.stderr)
        return 6

    path = Path(args.db).expanduser()
    if not path.exists():
        print(f"库不存在：{path}", file=sys.stderr)
        return 2
    try:
        conn = open_ro(path)
    except sqlite3.Error as e:
        print(f"库不可用（打开失败）：{path}：{e}", file=sys.stderr)
        return 2
    try:
        v0 = data_version(conn)
        generated = generate_questions(conn, n=args.n, seed=args.seed)
        stats = load_stats(conn)
        evaluation = evaluate(conn, generated["questions"], stats)
        unavailable = unavailable_report(conn)
        v1 = data_version(conn)
    except sqlite3.Error as e:
        print(f"库不可用（查询失败，可能缺表）：{path}：{e}", file=sys.stderr)
        return 2
    finally:
        conn.close()

    total = evaluation["overall"]
    report = {
        "meta": {
            "db": str(path), "seed": args.seed, "requested": args.n,
            "readonly": {"before": v0, "after": v1, "consistent": v1 == v0},
            "stats": stats, "generated_at_utc": utc(time.time()),
        },
        "questions": generated["questions"],
        "pool_sizes": generated["pool_sizes"],
        "dropped": generated["dropped"],
        "evaluation": evaluation,
        "unavailable": unavailable,
    }
    # 判定一律先于打印：库真被写了就不能先发一份宣称一致的报告。
    if v1 != v0:
        print(f"错误：评测期间库被写入（PRAGMA data_version {v0} → {v1}），报告作废。",
              file=sys.stderr)
        return 2
    if total["n"] == 0:
        print(f"错误：空池——生成 0 道题（请求 {args.n} 道），库不可用于评测。", file=sys.stderr)
        return 4
    print(render_markdown(report))

    if args.json:
        args.json.expanduser().write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nJSON 报告已写入：{args.json}", file=sys.stderr)
    if total["recalc_ok"] != total["n"] or total["evidence_passed"] != total["evidence_total"]:
        print(f"错误：尺子自检失败（复算 {total['recalc_ok']}/{total['n']}，"
              f"引用 {total['evidence_passed']}/{total['evidence_total']}），题集不能用。",
              file=sys.stderr)
        return 5
    return 0


if __name__ == "__main__":
    sys.exit(main())
