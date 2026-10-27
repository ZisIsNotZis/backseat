"""L1 评测尺子：从已冻结的历史库生成“答案唯一”的题集（无模型、无网络、无文件读取）。

题面只落在现有字段上：observations 的 key（帧 phash）与 ts_wall、messages 中
level='small' 行的 ts_start / content / obs_ids。每道题带一条 check_sql，它能在同一库
上独立复算出答案——这就是“引用可核”的落脚点。

答案唯一性在生成期用聚合查询证明：先用 GROUP BY 拿到重复值集合，再只挑不重复的候选；
重复答案的题直接丢弃，不靠假设。生成器对每道入选题还会用 check_sql 逐条复算一次，
复算不出同一答案的题同样丢弃。

时间一律用 UTC 表示（SQLite 的 datetime(ts,'unixepoch') 就是 UTC），题面、答案、
check_sql 三处口径一致。

计分与引用判定按 docs/2_GOAL.md 的 D2 度量定义：`score` 判单题（时间题 ±60s 容差，
其余规范化后相等），`evidence_pass` 判单条引用（被引单元真实持有声明值、该题 check_sql
能复算回答案、且该引用的时间落在答案所述区间 `interval`）。
"""

from __future__ import annotations

import calendar
import hashlib
import random
import sqlite3
import time

KINDS = ("time", "count", "content", "cite")

TIME_TOLERANCE_S = 60    # D2：时间题判对允许 ±60s 容差

CONTENT_PREFIX = 32      # content 题答案取 content 的前 N 个字符（SQL substr 可复算）
MIN_WINDOW_MSGS = 3      # count 题窗口内至少这么多条 small 消息
MIN_WINDOW_KEYS = 2      # count 题窗口内至少这么多个不同 phash
POOL_PER_KIND = 400      # 每题型候选池上限（生成期只在整表上做聚合，候选按步长跨取）

# evidence 要引用的原始单元：表 -> 允许的列。查询拼接只从这里取名字，别处不接受表名。
_EVIDENCE_COLUMNS = {
    "observations": ("id", "key", "ts_wall", "hash"),
    "messages": ("id", "ts_start", "content", "obs_ids", "level", "type"),
}

# L1 现缺的字段：这些题型不许假装能生成。probe_sql 由 run_eval 执行，把“缺”变成可核的数。
UNAVAILABLE_FIELDS = [
    {"kind": "窗口标题序列题",
     "missing": "observations 没有窗口标题列（M1 的活动窗口标题采集未落地）；标题只是事件载荷碎片，不构成字段",
     "effect": "无法生成“某时段活跃窗口是什么 / 窗口序列”这类题",
     "probe_sql": "SELECT count(*) FROM observations WHERE meta LIKE '%window_switch%'"},
    {"kind": "ATSPI 文本题（atspi.text_diff / atspi.tree_diff）",
     "missing": "传感器只有 screen 一种；observations 没有 atspi 行，content 列为空",
     "effect": "无法生成“屏幕上的文字事实是什么”这类题",
     "probe_sql": "SELECT count(*) FROM observations WHERE sensor='atspi'"},
    {"kind": "进程 / 仓库 / 文件系统事件题",
     "missing": "没有 proc / git / fs 传感器的行",
     "effect": "无法生成“哪个进程/仓库在动、改了哪些文件”这类题",
     "probe_sql": "SELECT count(*) FROM observations WHERE sensor IN ('proc','git','fs')"},
    {"kind": "决策点 / 纠正 / 离开标签题（labels 表）",
     "missing": "库中没有 labels 表（SCHEMA.md 的 labels 契约尚未落表）",
     "effect": "无法生成“用户在此处做了什么决策/纠正”这类题",
     "probe_sql": "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='labels'"},
    {"kind": "跨级回指题（medium / big 的 obs_ids 并集）",
     "missing": "medium / big 行的 obs_ids 全为 NULL，只有 small 行带证据引用",
     "effect": "无法生成“长程摘要回指到哪几帧”这类题",
     "probe_sql": "SELECT count(*) FROM messages WHERE level<>'small' AND obs_ids IS NOT NULL"},
    {"kind": "帧内容 / 尺寸题",
     "missing": "observations.content 全为空；帧本体是 ref 指向的文件路径（本包不读盘）",
     "effect": "无法生成“这一帧里有什么”这类题",
     "probe_sql": "SELECT count(*) FROM observations WHERE content IS NOT NULL AND content<>''"},
]


def utc(ts: float) -> str:
    """墙钟秒 -> UTC 'YYYY-MM-DD HH:MM:SS'，与 SQLite datetime(ts,'unixepoch') 同口径。"""
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(ts))


def utc_exact(ts: float) -> str:
    """墙钟秒 -> UTC 'YYYY-MM-DD HH:MM:SS.ffffff'，就是 query 里那个浮点端点的写法。

    题面窗口端点必须与 check_sql 的端点同值：用 utc() 截断到秒会让落在
    [整秒, 真实端点) 残段里的行出现在题面窗口内、却在答案窗口外。
    该格式只有微秒分辨率，所以它是对浮点端点的舍入（误差 ≤0.5µs），不是逐位相等。
    """
    whole = int(ts // 1)
    micro = int(round((ts - whole) * 1_000_000))
    if micro >= 1_000_000:  # 浮点舍入把小数顶到整秒
        whole, micro = whole + 1, 0
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(whole)) + f".{micro:06d}"


def normalize(text) -> str:
    """D2 的字符串规范化：去首尾空白、统一小写、压缩连续空白。"""
    return " ".join(str(text).split()).lower()


def parse_utc(text) -> float | None:
    """UTC 'YYYY-MM-DD HH:MM:SS' -> epoch 秒；解析不了返回 None。"""
    try:
        return calendar.timegm(time.strptime(normalize(text), "%Y-%m-%d %H:%M:%S"))
    except (TypeError, ValueError):
        return None


def score(question: dict, answer) -> bool:
    """D2 的单题判定：time 题按 ±60s 容差比较时刻，其余规范化后相等。"""
    if question["kind"] == "time":
        got, want = parse_utc(answer), parse_utc(question["answer"])
        if got is not None and want is not None:
            return abs(got - want) <= TIME_TOLERANCE_S
    return normalize(answer) == normalize(question["answer"])


def _second_interval(ts: float) -> dict:
    """把题面所述的一个时刻写成那一秒：[sec, sec+1)，与 utc() 的截断一致。"""
    start = int(ts // 1)
    return {"start": start, "end": start + 1, "end_inclusive": False}


def in_interval(ts: float, interval: dict | None) -> bool:
    """证据时间是否落在题面所述区间内；端点含否按 interval 自己的声明。"""
    if interval is None:
        return True
    if ts < interval["start"]:
        return False
    if interval["end_inclusive"]:
        return ts <= interval["end"]
    return ts < interval["end"]


def _sql_lit(value) -> str:
    """把值写成能直接粘进 sqlite3 CLI 的 SQL 字面量（check_sql 因此可独立复算）。"""
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)  # repr 对 double 是往返精确的，且本身就是合法数值字面量
    raise TypeError(f"不支持的 SQL 字面量类型：{type(value)!r}")


def _spread(rows: list, limit: int) -> list:
    """从整表结果里等步长跨取，避免候选池只落在时间线/字典序的一端。"""
    if len(rows) <= limit:
        return rows
    step = len(rows) / limit
    return [rows[int(i * step)] for i in range(limit)]


def evidence_exists(conn: sqlite3.Connection, ev: dict) -> bool:
    """一条证据引用是否在库里真实存在：该表的该列有这一格。"""
    cols = _EVIDENCE_COLUMNS.get(ev.get("table", ""))
    if cols is None or ev.get("column") not in cols:
        return False
    row = conn.execute(
        f"SELECT count(*) FROM {ev['table']} WHERE {ev['column']}=?", (ev["value"],)).fetchone()
    return int(row[0]) > 0


def recalc(conn: sqlite3.Connection, question: dict) -> str | None:
    """用 check_sql 独立复算答案；结果不是单行单列时返回 None（题面不唯一）。

    这里只认库里的复算值，绝不回显 question['answer']：短路的复算会让整把尺子自证。
    """
    rows = conn.execute(question["check_sql"]).fetchall()
    if len(rows) != 1 or len(rows[0]) != 1:
        return None
    return None if rows[0][0] is None else str(rows[0][0])


_EVIDENCE_TIME_COLUMN = {"observations": "ts_wall", "messages": "ts_start"}


def evidence_times(conn: sqlite3.Connection, ev: dict) -> list[float]:
    """这条引用能落到的时间：被引单元所在行的时间（非时间列则取该列所有命中行）。"""
    cols = _EVIDENCE_COLUMNS.get(ev.get("table", ""))
    if cols is None or ev.get("column") not in cols:
        return []
    tcol = _EVIDENCE_TIME_COLUMN[ev["table"]]
    if ev["column"] == tcol:
        return [float(ev["value"])]
    rows = conn.execute(
        f"SELECT {tcol} FROM {ev['table']} WHERE {ev['column']}=?", (ev["value"],)).fetchall()
    return [float(r[0]) for r in rows]


def evidence_pass(conn: sqlite3.Connection, question: dict, ev: dict) -> bool:
    """D2 的单条引用判定。三个条件同时成立才算通过：

    1. 被引单元真实持有声明值（引用不是编造的）；
    2. 该题 check_sql 在同一库上复算出的值就是答案（复算不一致则这条引用支撑不了任何东西）；
    3. 这条引用的时间落在答案所述区间 question['interval'] 内。
    """
    if not evidence_exists(conn, ev):
        return False
    if recalc(conn, question) != question["answer"]:
        return False
    return any(in_interval(t, question["interval"]) for t in evidence_times(conn, ev))


def verify_question(conn: sqlite3.Connection, question: dict) -> dict:
    """尺子自检：check_sql 复算 + 逐条引用的 D2 判定，返回计数供报告聚合。"""
    verdicts = [evidence_pass(conn, question, ev) for ev in question["evidence"]]
    return {"recalc_ok": recalc(conn, question) == question["answer"],
            "evidence_passed": sum(verdicts),
            "evidence_total": len(verdicts),
            "evidence_exists": bool(verdicts) and all(
                evidence_exists(conn, ev) for ev in question["evidence"])}


def _candidate(kind: str, question: str, answer: str, evidence: list,
               check_sql: str, interval: dict, finalize: tuple | None = None) -> dict:
    return {"kind": kind, "question": question, "answer": answer,
            "evidence": evidence, "check_sql": check_sql, "interval": interval,
            "_finalize": finalize}


def _finalize(conn: sqlite3.Connection, cand: dict) -> tuple[dict | None, str | None]:
    """选定一道题：补齐证据引用，并用 check_sql 复算一次。

    返回 (题, None)；被拒时返回 (None, 原因)，两条拒绝原因互相独立、分开计数：
    极值那一秒不唯一记 `second_not_unique`，check_sql 复算与答案不一致记 `recalc_mismatch`。
    """
    evidence = list(cand["evidence"])
    tag = cand.get("_finalize")
    if tag is not None:
        key, second = tag
        rows = conn.execute(
            "SELECT id FROM observations WHERE key=? "
            "AND substr(datetime(ts_wall,'unixepoch'),1,19)=?", (key, second)).fetchall()
        if len(rows) != 1:  # 极值那一秒不唯一 → 这道题没有唯一答案
            return None, "second_not_unique"
        evidence.append({"table": "observations", "column": "id", "value": rows[0]["id"]})
    out = {"kind": cand["kind"], "question": cand["question"], "answer": cand["answer"],
           "evidence": evidence, "check_sql": cand["check_sql"],
           "interval": cand["interval"]}
    if recalc(conn, out) != out["answer"]:
        return None, "recalc_mismatch"
    return out, None


def _time_candidates(conn: sqlite3.Connection) -> tuple[list[dict], list[dict]]:
    """time 题：某帧 key 的首次或末次出现时刻。唯一性 = 该极值所在那一秒只对应一行。"""
    rows = conn.execute(
        "SELECT key, count(*) c, min(ts_wall) mn, max(ts_wall) mx FROM observations "
        "WHERE key IS NOT NULL GROUP BY key ORDER BY key").fetchall()
    dup = {(r["key"], r["s"]) for r in conn.execute(
        "SELECT key, substr(datetime(ts_wall,'unixepoch'),1,19) s FROM observations "
        "WHERE key IS NOT NULL GROUP BY key, s HAVING count(*)>1")}
    out: list[dict] = []
    dropped = {"kind": "time", "reason": "extremum_second_not_unique", "count": 0}
    for r in _spread(rows, POOL_PER_KIND):
        key, count, first, last = r["key"], r["c"], r["mn"], r["mx"]
        for label, ts, fn in (("首次", first, "min"), ("末次", last, "max")):
            answer = utc(ts)
            if (key, answer) in dup:
                dropped["count"] += 1
                continue
            out.append(_candidate(
                "time",
                f"帧指纹 {key} 在 observations 中共出现 {count} 次。"
                f"它的{label}出现（ts_wall）是哪个时刻？回答 UTC 'YYYY-MM-DD HH:MM:SS'。",
                answer,
                [{"table": "observations", "column": "key", "value": key}],
                f"SELECT substr(datetime({fn}(ts_wall),'unixepoch'),1,19) "
                f"FROM observations WHERE key={_sql_lit(key)}",
                interval=_second_interval(ts), finalize=(key, answer)))
    return out, [dropped] if dropped["count"] else []


def _count_candidates(conn: sqlite3.Connection) -> tuple[list[dict], list[dict]]:
    """count 题：连续两个 UTC 日首条 small 消息之间的半开窗口内的条数/不同 phash 数。

    窗口写成 [t0, t1) 并在题面里写明含左端、不含右端；题面端点用 utc_exact() 写出与
    check_sql 同源的原始浮点的微秒写法（舍入 ≤0.5µs），不用截断到秒的 utc()；答案与计分
    区间用原始浮点，不受该舍入影响。引用的证据只取**落在窗口内**的真实行（窗口首、末一条），
    不引 t1 那一行——它不在窗口里，引它就和题面互相矛盾。
    聚合恒返回单行单值；Python 侧另用一次“列出该窗口内的行”得到答案，与 SQL 聚合互不依赖。
    """
    days = conn.execute(
        "SELECT date(ts_start,'unixepoch') d, min(ts_start) t0 FROM messages "
        "WHERE level='small' GROUP BY d ORDER BY d").fetchall()
    out: list[dict] = []
    dropped = {"kind": "count", "reason": "window_too_small", "count": 0}
    for a, b in zip(days, days[1:]):
        t0, t1 = a["t0"], b["t0"]
        msg_ids = [r[0] for r in conn.execute(
            "SELECT id FROM messages WHERE level='small' AND ts_start>=? AND ts_start<?",
            (t0, t1))]
        keys = [r[0] for r in conn.execute(
            "SELECT DISTINCT key FROM observations WHERE key IS NOT NULL "
            "AND ts_wall>=? AND ts_wall<?", (t0, t1))]
        if len(msg_ids) < MIN_WINDOW_MSGS or len(keys) < MIN_WINDOW_KEYS:
            dropped["count"] += 1
            continue
        window = f"[{utc_exact(t0)}, {utc_exact(t1)})"
        interval = {"start": t0, "end": t1, "end_inclusive": False}
        msg_rows = conn.execute(
            "SELECT id FROM messages WHERE level='small' AND ts_start>=? AND ts_start<? "
            "ORDER BY ts_start, id", (t0, t1)).fetchall()
        obs_rows = conn.execute(
            "SELECT id FROM observations WHERE key IS NOT NULL AND ts_wall>=? AND ts_wall<? "
            "ORDER BY ts_wall, id", (t0, t1)).fetchall()
        msg_ev = [{"table": "messages", "column": "id", "value": msg_rows[0][0]},
                  {"table": "messages", "column": "id", "value": msg_rows[-1][0]}]
        obs_ev = [{"table": "observations", "column": "id", "value": obs_rows[0][0]},
                  {"table": "observations", "column": "id", "value": obs_rows[-1][0]}]
        out.append(_candidate(
            "count",
            f"在 UTC 半开窗口 {window}（含左端、不含右端）内，level='small' 的消息有多少条？回答整数。",
            str(len(msg_ids)), msg_ev,
            "SELECT count(*) FROM messages WHERE level='small' "
            f"AND ts_start>={_sql_lit(t0)} AND ts_start<{_sql_lit(t1)}",
            interval=interval))
        out.append(_candidate(
            "count",
            f"同一 UTC 半开窗口 {window}（含左端、不含右端）内，observations 有多少个不同帧 key"
            f"（phash）？回答整数。",
            str(len(set(keys))), obs_ev,
            "SELECT count(DISTINCT key) FROM observations WHERE key IS NOT NULL "
            f"AND ts_wall>={_sql_lit(t0)} AND ts_wall<{_sql_lit(t1)}",
            interval=interval))
    return out, [dropped] if dropped["count"] else []


def _content_candidates(conn: sqlite3.Connection) -> tuple[list[dict], list[dict]]:
    """content 题：用（ts_start, type, content 的 sha256 前 12 位）定位一条 small 消息，答案取内容前缀。

    题干不出现 messages.id：那会把“描述再取回”偷成“按主键查表”。check_sql 走 SQL 的
    datetime/substr 定位，与 Python 侧的取行互不依赖。
    唯一性 = 该内容前缀在 small 消息里只出现一次，且该消息的 ts_start 那一秒只对应一条。
    """
    dup_prefix = {r["p"] for r in conn.execute(
        "SELECT substr(content,1,?) p FROM messages WHERE level='small' "
        "GROUP BY p HAVING count(*)>1", (CONTENT_PREFIX,))}
    dup_second = {r["s"] for r in conn.execute(
        "SELECT substr(datetime(ts_start,'unixepoch'),1,19) s FROM messages "
        "WHERE level='small' GROUP BY s HAVING count(*)>1")}
    rows = conn.execute(
        "SELECT id, ts_start, type, content FROM messages "
        "WHERE level='small' AND content IS NOT NULL AND content<>'' ORDER BY id").fetchall()
    out: list[dict] = []
    dropped = {"kind": "content", "reason": "content_prefix_or_second_not_unique", "count": 0}
    for r in _spread(rows, POOL_PER_KIND):
        content, second = r["content"], utc(r["ts_start"])
        prefix = content[:CONTENT_PREFIX]
        if prefix in dup_prefix or second in dup_second:
            dropped["count"] += 1
            continue
        sha = hashlib.sha256(content.encode("utf-8")).hexdigest()[:12]
        type_pred = "type IS NULL" if r["type"] is None else f"type={_sql_lit(r['type'])}"
        out.append(_candidate(
            "content",
            f"有一条 level='small' 的消息：ts_start={second} UTC，type={r['type'] or '未标'}，"
            f"content 的 sha256 前 12 位={sha}。它的 content 前 {CONTENT_PREFIX} 个字符是什么？",
            prefix,
            [{"table": "messages", "column": "id", "value": r["id"]},
             {"table": "messages", "column": "ts_start", "value": r["ts_start"]}],
            f"SELECT substr(content,1,{CONTENT_PREFIX}) FROM messages "
            "WHERE level='small' AND content IS NOT NULL AND content<>'' "
            f"AND {type_pred} "
            f"AND substr(datetime(ts_start,'unixepoch'),1,19)='{second}'",
            interval=_second_interval(r["ts_start"])))
    return out, [dropped] if dropped["count"] else []


def _cite_candidates(conn: sqlite3.Connection) -> tuple[list[dict], list[dict]]:
    """cite 题：给一条 small 消息，沿 obs_ids 回指到 observations 行，答案取该帧的 key。

    唯一性 = 该消息的 obs_ids 恰好一项，且该项在 observations 里恰好命中一行（主键）。
    """
    rows = conn.execute(
        "SELECT m.id, m.ts_start, m.type, m.obs_ids, o.key, o.id oid FROM messages m "
        "JOIN observations o ON o.id=json_extract(m.obs_ids,'$[0]') "
        "WHERE m.level='small' AND json_array_length(m.obs_ids)=1 ORDER BY m.id").fetchall()
    single = conn.execute(
        "SELECT count(*) FROM messages WHERE level='small' "
        "AND coalesce(json_array_length(obs_ids),0)=1").fetchone()[0]
    dropped = []
    multi = conn.execute(
        "SELECT count(*) FROM messages WHERE level='small' "
        "AND coalesce(json_array_length(obs_ids),0)<>1").fetchone()[0]
    if multi:
        dropped.append({"kind": "cite", "reason": "obs_ids_not_single", "count": int(multi)})
    if single > len(rows):
        dropped.append({"kind": "cite", "reason": "obs_id_missing",
                        "count": int(single - len(rows))})
    out = []
    for r in _spread(rows, POOL_PER_KIND):
        out.append(_candidate(
            "cite",
            f"small 消息 id={r['id']}（ts_start={utc(r['ts_start'])} UTC，"
            f"type={r['type'] or '未标'}）的 obs_ids 只有一项。"
            f"它回指的 observations 行的 key（帧指纹）是什么？",
            r["key"],
            [{"table": "messages", "column": "id", "value": r["id"]},
             {"table": "messages", "column": "obs_ids", "value": r["obs_ids"]}],
            "SELECT o.key FROM messages m JOIN json_each(m.obs_ids) j "
            "JOIN observations o ON o.id=j.value "
            f"WHERE m.id={_sql_lit(r['id'])} AND json_array_length(m.obs_ids)=1",
            interval=_second_interval(r["ts_start"])))
    return out, dropped


_BUILDERS = (("time", _time_candidates), ("count", _count_candidates),
            ("content", _content_candidates), ("cite", _cite_candidates))


def _allocate(pools: dict[str, list], n: int, rng: random.Random) -> dict[str, int]:
    """把 n 道题轮流分给各题型（池子小的题型拿满即止，余量转给还有候选的题型）。"""
    for kind in KINDS:
        rng.shuffle(pools[kind])
    quota = {kind: 0 for kind in KINDS}
    left = n
    while left > 0:
        progressed = False
        for kind in KINDS:
            if left > 0 and len(pools[kind]) > quota[kind]:
                quota[kind] += 1
                left -= 1
                progressed = True
        if not progressed:
            break
    return quota


def generate_questions(conn: sqlite3.Connection, n: int = 50,
                       seed: int = 0) -> dict:
    """生成至多 n 道题。返回 {questions, dropped, pool_sizes, requested}。

    dropped 按题型/原因汇总被丢弃的候选数：`second_not_unique`（极值那一秒不唯一）、
    `recalc_mismatch`（check_sql 复算与答案不一致）等生成期原因各记各的，报告据此说明
    每道题为什么没进题集，不把两类原因合成一个标签。
    """
    rng = random.Random(seed)
    pools: dict[str, list] = {}
    dropped: list[dict] = []
    for kind, build in _BUILDERS:
        cands, drops = build(conn)
        pools[kind] = cands
        dropped.extend(drops)

    # 同题型内答案去重：重复答案的题直接丢弃（跨题型答案空间不同，不互相比较）
    for kind in list(pools):
        seen: set[str] = set()
        uniq = []
        dup_n = 0
        for cand in pools[kind]:
            if cand["answer"] in seen:
                dup_n += 1
                continue
            seen.add(cand["answer"])
            uniq.append(cand)
        if dup_n:
            dropped.append({"kind": kind, "reason": "duplicate_answer", "count": dup_n})
        pools[kind] = uniq

    quota = _allocate(pools, n, rng)
    questions: list[dict] = []
    for kind in KINDS:
        picked = 0
        for cand in pools[kind]:
            if picked >= quota[kind]:
                break
            q, reason = _finalize(conn, cand)
            if q is None:
                dropped.append({"kind": kind, "reason": reason, "count": 1})
                continue
            q["id"] = f"{kind}-{picked:03d}"
            questions.append(q)
            picked += 1

    return {"questions": questions, "dropped": dropped,
            "pool_sizes": {kind: len(pools.get(kind, [])) for kind in KINDS},
            "requested": n}
