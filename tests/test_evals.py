"""L1 评测尺子冒烟：合成库上跑题集生成器 + run_eval（不依赖真实库）。

覆盖：四题型齐全、题面结构、check_sql 逐题复算、**手算期望答案**（不经生成器的 recalc
自证）、content 题干的 sha256[:12] 必须等于库里内容的**重算值**、D2 计分器（时间题 ±60s
容差 + 字符串规范化）、D2 逐条引用判定（窗口外的引用必须判失败，这是 count 题右端点 bug
的证伪点）、引用正确率与证据存在率两个独立指标、B1 全体 / B1 语义子集（cite 子集必须
全对）、答案唯一性检查确实丢弃重复候选、`_finalize` 的两条拒绝原因分开计数
（second_not_unique / recalc_mismatch，计数路径由桩真走一次）、
只读连接写不进去、退出码 0/2/4/5/6（6 必须先于打开库；v1!=v0 作废报告且 stdout 为空）、
报告文案随 data_version 比对结果分支。

运行：python3 tests/test_evals.py
"""

import calendar
import contextlib
import hashlib
import io
import json
import re
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from backseat.store import Store
from evals import questions as questions_mod
from evals import run_eval
from evals.questions import (
    KINDS, _candidate, _finalize, evidence_pass, generate_questions, in_interval, recalc,
    score, utc_exact, verify_question)

BASE = 1577836800.0   # 2020-01-01 00:00:00 UTC
DAY = 86400.0

# 手算的期望值：直接数合成库，不经过生成器的 answer 字段。
#   窗口 [2020-01-01 00:00:10, 2020-01-02 00:00:10) 内的 small 消息 = m1..m5 = 5 条
#   同一窗口内的 observations = a@+100, a@+200, b@+300, b@+300, c@+400, d@+500
#     → 6 行，不同 key = {a, b, c, d} = 4
#   m1 的 content 只有 15 个字，前 32 字就是全文
#   m6 回指的那一帧 key = phash:eeeeeeeeeeeeeeee
#   key a 的首次出现时刻 = 2020-01-01 00:01:40（BASE+100）
EXPECT_COUNT_MSGS = "5"
EXPECT_COUNT_KEYS = "4"
EXPECT_CONTENT_M1 = "第一天上午在写评测尺子的生成器"
EXPECT_CITE_E = "phash:" + "e" * 16
EXPECT_TIME_A_FIRST = "2020-01-01 00:01:40"

CHECKS = 0

# content 题干里的两个定位值，测试用来独立重算哈希（不看生成器写进题干的那个字符串）。
_SHA_RE = re.compile(r"sha256 前 12 位=([0-9a-f]{12})")
_TS_RE = re.compile(r"ts_start=(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) UTC")

# 同一秒两行的 key：`_finalize` 的唯一性分支只能靠它触发。
DUP_KEY = "phash:" + "b" * 16
DUP_SECOND = "2020-01-01 00:05:00"

# `_finalize` 拒掉候选的两条互不相干的原因，丢弃统计按这两条分别求和（不合并、不覆盖）。
FINALIZE_REASONS = ("second_not_unique", "recalc_mismatch")


def check(cond, msg) -> None:
    """计数断言：最后打印总数，报告里能说清“这次到底查了多少条”。"""
    global CHECKS
    CHECKS += 1
    assert cond, msg


def build(db: Path) -> dict:
    """两天的合成历史：一个 key 复现（a）、一个 key 同一秒重复（b）、
    一对内容前缀相同的消息（m2/m3）、一条 obs_ids 有两项的消息（m3）。"""
    store = Store(db)
    obs, msg = {}, {}

    def mk(name: str, wall: float) -> int:
        oid = store.insert("observations", ts_wall=wall, ts_mono=wall, sensor="screen",
                           kind="screen.notable", display="primary",
                           key=f"phash:{name * 16}", hash=f"phash:{name * 16}", bytes=10)
        obs[name] = oid
        return oid

    mk("a", BASE + 100)
    mk("a", BASE + 200)
    mk("b", BASE + 300)
    mk("b", BASE + 300)          # 同一秒同一 key → 极值那一秒不唯一
    mk("c", BASE + 400)
    mk("d", BASE + 500)
    mk("e", BASE + DAY + 100)
    mk("e", BASE + DAY + 200)

    def put(name: str, ts: float, content: str, obs_ids: list) -> int:
        mid = store.insert("messages", level="small", ts_start=ts, ts_end=ts,
                           content=content, intent="测试意图", type="diff",
                           source="vlm", obs_ids=obs_ids)
        msg[name] = mid
        return mid

    # m3 与 m2 的前 32 个字符相同 → 两条的 content 前缀都不唯一，生成期必须丢弃
    dup_content = "第一天下午在核对证据引用链、基线口径、缺失字段清单与报告骨架" * 2
    put("m1", BASE + 10, "第一天上午在写评测尺子的生成器", [obs["a"]])
    put("m2", BASE + 20, dup_content, [obs["b"]])
    put("m3", BASE + 30, dup_content + "的续写", [obs["a"], obs["a"]])
    put("m4", BASE + 40, "傍晚在补基线口径", [obs["c"]])
    put("m5", BASE + 50, "夜里在跑合成库自测", [obs["d"]])
    put("m6", BASE + DAY + 10, "第二天在整理缺失字段清单", [obs["e"]])
    put("m7", BASE + DAY + 20, "第二天下午在写报告骨架", [obs["e"]])
    store.close()
    return {"obs": obs, "msg": msg}


def db_digest(db: Path) -> str:
    """主库文件的字节哈希：内容有没有被改写只看这个。"""
    return hashlib.sha256(db.read_bytes()).hexdigest()


def wal_payload(db: Path) -> int:
    """WAL 里有多少字节的写入帧。只读连接会创建空的 -shm/-wal 索引，但那不是写入。"""
    p = Path(str(db) + "-wal")
    return p.stat().st_size if p.exists() else 0


def run_main(argv: list[str]) -> tuple[int, str, str]:
    """跑一次 CLI，回 (退出码, stdout, stderr)。"""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = run_eval.main(argv)
    return rc, out.getvalue(), err.getvalue()


def drop_counts(dropped: list[dict]) -> dict[tuple[str, str], int]:
    """按 (题型, 原因) **求和**聚合丢弃记录：同一原因多条记录必须相加，不许被后一条覆盖。"""
    out: dict[tuple[str, str], int] = {}
    for d in dropped:
        key = (d["kind"], d["reason"])
        out[key] = out.get(key, 0) + d["count"]
    return out


def check_finalize_identity(gen: dict, drops: dict) -> None:
    """池子 − 入选题 = 该题型被 `_finalize` 拒掉的候选数：不许有候选被悄悄吞掉。

    前提 `n ≥ sum(pool_sizes)`，即全池都在配额内：`generate_questions` 在
    `picked >= quota[kind]` 时直接 break，配额用尽后的剩余候选既不入选也不记丢弃，
    所以 n 小于全池时池子会大于“入选题 + 拒掉数”，本恒等式不成立。
    两处调用都用 n=100 跑全池，前提成立。
    """
    for kind in KINDS:
        picked = sum(1 for q in gen["questions"] if q["kind"] == kind)
        rejected = sum(drops.get((kind, reason), 0) for reason in FINALIZE_REASONS)
        check(gen["pool_sizes"][kind] == picked + rejected,
              ("丢弃统计没把 _finalize 的丢弃算进去（恒等式的前提是 n ≥ sum(pool_sizes)："
               "配额用尽后剩余候选既不入选也不记丢弃）", kind,
               gen["pool_sizes"][kind], picked, rejected))


def check_exit6_refuses_before_open(td: Path) -> None:
    """`--n 0` + 不存在的库路径：必须仍报参数误用（退出码 6），stderr 不提库。

    库路径不存在时先报“库不存在”就说明拦截发生在打开库之后；把 `--n` 的检查挪到 open_ro
    之后、generate_questions 之前，这条就红（库可开、也仍返回 6）。
    """
    rc, out, err = run_main(["--db", str(td / "nope.db"), "--n", "0"])
    check(rc == 6, (rc, err))
    check("库不存在" not in err and "库不可用" not in err,
          ("--n 误用必须在打开/检查库之前拦截", err))
    check(out == "", ("参数误用不应打印报告", out))
    print("退出码 6 的拦截点 OK: 库路径不存在也仍报参数误用，stderr 一个字不提库")


def check_invalidated_report(db: Path) -> None:
    """v1 != v0 时判定先于打印：退出码 2、stderr 说“报告作废”、stdout 一个字不出。"""
    real = run_eval.data_version
    seq = iter([1, 2])
    with patch.object(run_eval, "data_version", lambda conn: next(seq)):
        rc, out, err = run_main(["--db", str(db), "--n", "8"])
    check(run_eval.data_version is real, "data_version 桩必须在退出后自动还原，不得残留")
    check(rc == 2, (rc, err))
    check("报告作废" in err, ("v1!=v0 必须说报告作废", err))
    check(out == "", ("报告作废时不得打印任何报告", out[:80]))
    print("报告作废 OK: v1!=v0 → 退出码 2、stderr 说报告作废、stdout 为空")


def check_schema(qs: list[dict]) -> None:
    """题集结构与“题干不泄漏主键”。"""
    for q in qs:
        check(set(q) == {"id", "kind", "question", "answer", "evidence", "check_sql",
                         "interval"}, ("题集字段不符 D2 消费口径", q["id"], sorted(q)))
        check(q["evidence"] and all(
            set(ev) == {"table", "column", "value"} for ev in q["evidence"]),
            ("evidence 结构不符", q["id"]))
        check(q["interval"]["start"] < q["interval"]["end"], ("interval 不成立", q["id"]))
        if q["kind"] == "content":
            check("id=" not in q["question"],
                  ("content 题干泄漏了主键", q["question"]))
            check("sha256 前 12 位" in q["question"], ("content 题缺少哈希定位", q["question"]))
        if q["kind"] == "cite":
            check("id=" in q["question"], ("cite 题按裁决保留主键", q["question"]))
        if q["kind"] == "count":
            check("含左端、不含右端" in q["question"], ("count 题必须写明端点含否", q["question"]))


def check_hand_values(qs: list[dict]) -> None:
    """手算期望答案：断言值全部来自合成库的人工计数，不是生成器的 answer。"""
    by_kind = {kind: [q for q in qs if q["kind"] == kind] for kind in KINDS}

    count_answers = sorted(q["answer"] for q in by_kind["count"])
    check(count_answers == sorted([EXPECT_COUNT_MSGS, EXPECT_COUNT_KEYS]),
          ("count 题答案与手算不符", count_answers))

    content_answers = {q["answer"] for q in by_kind["content"]}
    check(EXPECT_CONTENT_M1 in content_answers, ("content 手算答案缺失", content_answers))
    check(len(EXPECT_CONTENT_M1) < 32, "手算前提：m1 的 content 短于 32 字")

    cite_answers = {q["answer"] for q in by_kind["cite"]}
    check(EXPECT_CITE_E in cite_answers, ("cite 手算答案缺失", cite_answers))

    time_qs = [q for q in by_kind["time"] if q["answer"] == EXPECT_TIME_A_FIRST]
    check(len(time_qs) == 1, ("time 手算答案缺失", [q["answer"] for q in by_kind["time"]]))
    check("共出现 2 次" in time_qs[0]["question"], ("time 题干出现次数与手算不符",
                                                    time_qs[0]["question"]))


def check_recalc_is_independent(conn: sqlite3.Connection, qs: list[dict]) -> None:
    """尺子自检本身是可证伪的：check_sql 的原始结果必须等于答案，recalc 不得回显答案。"""
    for q in qs:
        row = conn.execute(q["check_sql"]).fetchall()
        check(len(row) == 1 and len(row[0]) == 1 and str(row[0][0]) == q["answer"],
              ("check_sql 原始结果与答案不符（绕过 recalc 直接查）", q["id"], row))
    probe = next(q for q in qs if q["kind"] == "count")
    lying = dict(probe, answer="999")
    check(recalc(conn, lying) == probe["answer"],
          ("recalc 必须返回库里的真值，不能回显题目的 answer", recalc(conn, lying)))
    check(_finalize(conn, _candidate(probe["kind"], probe["question"], "999",
                                     probe["evidence"], probe["check_sql"],
                                     probe["interval"])) == (None, "recalc_mismatch"),
          "复算不一致的候选必须被 _finalize 拒成 recalc_mismatch（不是唯一性）")


def check_content_hash(conn: sqlite3.Connection, qs: list[dict]) -> None:
    """content 题干的 sha256[:12] 必须等于库里那条 content 的重算值。

    题干里的哈希当前只是定位装饰（check_sql 不用它），所以测试自己重算：哈希口径一旦漂移
    （编码、长度、传给 hexdigest 的串变了），这里就红，而不是留下一句无人校验的话。
    """
    seen = 0
    for q in qs:
        if q["kind"] != "content":
            continue
        sha, second = _SHA_RE.search(q["question"]), _TS_RE.search(q["question"])
        check(sha is not None and second is not None,
              ("content 题干缺少哈希或时刻定位", q["id"], q["question"]))
        rows = conn.execute(
            "SELECT content FROM messages WHERE level='small' "
            "AND content IS NOT NULL AND content<>'' "
            "AND substr(datetime(ts_start,'unixepoch'),1,19)=?", (second.group(1),)).fetchall()
        check(len(rows) == 1,
              ("content 题的（秒）定位不唯一，题干哈希无从校验", q["id"], len(rows)))
        recomputed = hashlib.sha256(rows[0]["content"].encode("utf-8")).hexdigest()[:12]
        check(recomputed == sha.group(1),
              ("content 题干声明的 sha256 前 12 位与库内容重算不符",
               q["id"], sha.group(1), recomputed))
        seen += 1
    check(seen > 0, "合成库里应该至少有一道 content 题可校验哈希")


def check_finalize_uniqueness(conn: sqlite3.Connection, whole: dict) -> None:
    """`_finalize` 的唯一性分支（len(rows)!=1）必须真被触发，并拒成 second_not_unique。"""
    dup_rows = conn.execute(
        "SELECT count(*) FROM observations WHERE key=? "
        "AND substr(datetime(ts_wall,'unixepoch'),1,19)=?",
        (DUP_KEY, DUP_SECOND)).fetchone()[0]
    check(dup_rows == 2, ("合成库前提：该 key 那一秒必须有两行", dup_rows))
    # check_sql 复算回来的就是 answer，所以拒绝只能来自唯一性分支，不可能来自复算不一致。
    cand = _candidate(
        "time", "同一秒两行的 key 的末次出现时刻？", DUP_SECOND, [],
        "SELECT substr(datetime(max(ts_wall),'unixepoch'),1,19) "
        f"FROM observations WHERE key='{DUP_KEY}'",
        {"start": BASE + 300, "end": BASE + 301, "end_inclusive": False},
        finalize=(DUP_KEY, DUP_SECOND))
    check(_finalize(conn, cand) == (None, "second_not_unique"),
          "_finalize 必须把同一秒两行的 key 拒成 second_not_unique")
    # 真实池上两条 finalize 原因各自应为 0；恒等式仍必须成立。
    check_finalize_identity(whole, drop_counts(whole["dropped"]))


def check_drop_counting(conn: sqlite3.Connection, whole: dict) -> None:
    """丢弃计数路径必须真被走一次：桩掉 `_finalize` 拒掉首个 time 候选，该 reason +1。

    没有这一步，夹具上 `_finalize` 从不返回 None，恒等式退化成 `池子 == 入选题`，
    删掉 `generate_questions` 里 append 丢弃记录那一行套件仍然全绿。
    """
    real_finalize = questions_mod._finalize
    rejected: list[dict] = []

    # 聚合必须是求和而不是字典覆盖：同一个 (题型, 原因) 的多条记录要相加。
    check(drop_counts([{"kind": "time", "reason": "second_not_unique", "count": 1},
                       {"kind": "time", "reason": "second_not_unique", "count": 1}])
          == {("time", "second_not_unique"): 2},
          "同一 (题型, 原因) 的多条丢弃记录必须相加，不能被后一条覆盖")

    def fake_finalize(conn_: sqlite3.Connection, cand: dict):
        if cand["kind"] == "time" and not rejected:
            rejected.append(cand)
            return None, "second_not_unique"
        return real_finalize(conn_, cand)

    with patch.object(questions_mod, "_finalize", fake_finalize):
        stubbed = generate_questions(conn, n=100, seed=1)
        check(questions_mod._finalize is fake_finalize,
              "桩必须替换 generate_questions 实际解析到的那个名字")
    check(questions_mod._finalize is real_finalize, "桩必须在退出后自动还原，不得残留")
    check(len(rejected) == 1, ("桩必须真被调用一次", len(rejected)))
    drops = drop_counts(stubbed["dropped"])
    check(drops.get(("time", "second_not_unique"), 0) == 1,
          ("_finalize 拒掉的候选必须按 reason 记一条", drops))
    check(len(stubbed["questions"]) == len(whole["questions"]) - 1,
          ("拒掉一道候选就少一道题", len(stubbed["questions"]), len(whole["questions"])))
    check_finalize_identity(stubbed, drops)
    print(f"丢弃原因计数 OK: 桩拒掉 1 个 time 候选 → second_not_unique="
          f"{drops[('time', 'second_not_unique')]}，恒等式仍成立，桩已还原")


def check_d2_scoring(qs: list[dict]) -> None:
    """D2 计分器：时间题 ±60s 容差，其余规范化后相等。"""
    time_q = next(q for q in qs if q["kind"] == "time" and q["answer"] == EXPECT_TIME_A_FIRST)
    check(score(time_q, EXPECT_TIME_A_FIRST), "同一时刻必须判对")
    check(score(time_q, "2020-01-01 00:02:40"), "±60s 内必须判对")
    check(score(time_q, "2020-01-01 00:00:40"), "±60s 内（负向）必须判对")
    check(not score(time_q, "2020-01-01 00:02:41"), "超出 ±60s 必须判错")
    check(not score(time_q, "2020-01-01T00:01:40Z"), "非规范时间格式按字符串比，判错")
    count_q = next(q for q in qs if q["kind"] == "count")
    check(score(count_q, f"  {count_q['answer']}  "), "规范化必须去首尾空白")
    check(score(count_q, f"{count_q['answer']}\t\n"), "规范化必须压缩空白")
    check(not score(count_q, f"{count_q['answer']}.0"), "整数答案不接受小数写法")
    content_q = next(q for q in qs if q["kind"] == "content")
    check(score(content_q, content_q["answer"]), "非时间题同一答案必须判对")


def check_d2_evidence(conn: sqlite3.Connection, qs: list[dict], ids: dict) -> None:
    """D2 逐条引用：每条引用都要过，且窗口外的引用必须判失败（N2 的证伪点）。"""
    for q in qs:
        res = verify_question(conn, q)
        check(res["recalc_ok"], ("复算失败", q["id"]))
        check(res["evidence_passed"] == res["evidence_total"],
              ("有引用判失败", q["id"], res))
        check(res["evidence_exists"], ("证据单元不存在", q["id"]))

    count_q = next(q for q in qs if q["kind"] == "count"
                   and q["answer"] == EXPECT_COUNT_MSGS)
    in_window = {"table": "messages", "column": "id", "value": ids["msg"]["m1"]}
    # m6 的 ts_start 就是窗口右端 t1：题面写明“不含右端”，引它必然自相矛盾。
    at_right_end = {"table": "messages", "column": "id", "value": ids["msg"]["m6"]}
    check(evidence_pass(conn, count_q, in_window), "窗口内的消息引用必须通过")
    check(not evidence_pass(conn, count_q, at_right_end),
          "引用了窗口右端（不含）那一行的引用必须判失败")
    outside = {"table": "observations", "column": "id", "value": ids["obs"]["e"]}
    check(not evidence_pass(conn, count_q, outside), "窗口外的观察引用必须判失败")


def check_window_endpoints(db: Path) -> None:
    """count 题题干端点必须就是 check_sql 的**精确浮点端点**（不是截断到整秒的 utc()）。

    亚秒时间戳下两种口径会分出真假：题干写整秒 [00:00:10, …) 时，落在
    [00:00:10.000000, 00:00:10.250000) 残段里的 observation 在题面窗口内、却在答案窗口外。
    """
    t0, t1 = BASE + 10.25, BASE + DAY + 10.25
    store = Store(db)

    def mk(wall: float, name: str) -> int:
        return store.insert("observations", ts_wall=wall, ts_mono=wall, sensor="screen",
                            kind="screen.notable", display="primary",
                            key=f"phash:{name * 16}", hash=f"phash:{name * 16}", bytes=10)

    mk(BASE + 10.1, "r")          # 亚秒残段：只在“截断到秒”的题面窗口里
    for name, wall in (("x", BASE + 20.0), ("y", BASE + 30.0), ("z", BASE + 40.0)):
        mk(wall, name)
    for name, ts in (("q1", t0), ("q2", BASE + 20.0), ("q3", BASE + 30.0),
                     ("q4", BASE + 40.0), ("q5", t1)):
        store.insert("messages", level="small", ts_start=ts, ts_end=ts,
                     content=f"亚秒夹具 {name}", intent="测试意图", type="diff",
                     source="vlm", obs_ids=None)
    store.close()

    check(int(BASE + 10.1) == int(t0) and BASE + 10.1 < t0,
          "亚秒残段前提：残段行属于起点那一秒但早于真实端点")
    conn = run_eval.open_ro(db)
    try:
        count_qs = [q for q in generate_questions(conn, n=100, seed=0)["questions"]
                    if q["kind"] == "count"]
    finally:
        conn.close()
    check(len(count_qs) == 2, ("亚秒夹具应生成两道 count 题", len(count_qs)))
    for q in count_qs:
        check(f"{utc_exact(t0)}, {utc_exact(t1)}" in q["question"],
              ("count 题干端点必须写精确浮点值（不是截断到秒）", q["question"]))
        got = re.search(r"\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d(?:\.\d+)?), "
                        r"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d(?:\.\d+)?)\)", q["question"])
        check(got is not None, ("count 题干缺窗口端点", q["question"]))
        for text, want in zip(got.groups(), (q["interval"]["start"], q["interval"]["end"])):
            base, _, frac = text.partition(".")
            parsed = (calendar.timegm(time.strptime(base, "%Y-%m-%d %H:%M:%S"))
                      + (int(frac.ljust(6, "0")) / 1e6 if frac else 0.0))
            check(abs(parsed - want) < 1e-6,
                  ("count 题干端点与计分区间不同值", q["id"], text, want))
        # 题面窗口与答案窗口同口径：残段里那行不在答案里，也不能被题面说成在内。
        check(not in_interval(BASE + 10.1, q["interval"]),
              ("残段里的行不得落入计分区间", q["id"]))
    keys_q = next(q for q in count_qs if "不同帧 key" in q["question"])
    check(keys_q["answer"] == "3", ("残段里的 key 不得计入答案", keys_q["answer"]))
    msgs_q = next(q for q in count_qs if "消息" in q["question"])
    check(msgs_q["answer"] == "4", ("窗口内 small 消息数", msgs_q["answer"]))
    print(f"窗口端点口径 OK: 题干写 [{utc_exact(t0)}, {utc_exact(t1)})，"
          f"与 check_sql 同源于原始浮点（微秒写法舍入 ≤0.5µs，不是同值）；"
          f"残段行 {BASE + 10.1:.2f} 不在窗口内（不同 key 答案 "
          f"{keys_q['answer']} 已排除它）")


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "eval.db"
        ids = build(db)
        before_digest, before_wal = db_digest(db), wal_payload(db)

        # --- 亚秒夹具：count 题题干端点与 check_sql 同源于原始浮点 ---
        check_window_endpoints(Path(td) / "fractional.db")

        # --- 生成器：题型齐全 + 题面结构 + 逐题复算 ---
        conn = run_eval.open_ro(db)
        check(conn.execute("PRAGMA query_only").fetchone()[0] == 1,
              "只读连接必须 query_only")
        gen = generate_questions(conn, n=8, seed=0)
        qs = gen["questions"]
        check({q["kind"] for q in qs} == set(KINDS), [q["kind"] for q in qs])
        check(len(qs) == 8, len(qs))
        print(f"生成器 OK: 题型齐全 {sorted({q['kind'] for q in qs})}，"
              f"check_sql 逐题复算 8/8，证据引用全部存在")

        # --- 整池（n=100 即全池）上的手算期望值与 D2 判定 ---
        whole_gen = generate_questions(conn, n=100, seed=1)
        whole = whole_gen["questions"]
        check(len(whole) > 8, ("全池题量异常", len(whole)))
        check_schema(whole)
        check_hand_values(whole)
        check_recalc_is_independent(conn, whole)
        check_content_hash(conn, whole)
        check_finalize_uniqueness(conn, whole_gen)
        check_drop_counting(conn, whole_gen)
        check_d2_scoring(whole)
        check_d2_evidence(conn, whole, ids)
        print("手算期望答案 OK: "
              f"count 消息数={EXPECT_COUNT_MSGS}、count 不同 key={EXPECT_COUNT_KEYS}、"
              f"content(m1)={EXPECT_CONTENT_M1}、cite(m6)={EXPECT_CITE_E}、"
              f"time(a 首次)={EXPECT_TIME_A_FIRST}；content 题干的 sha256[:12] 与库内容重算一致；"
              "_finalize 的唯一性分支被拒成 second_not_unique（与 recalc_mismatch 分开）")

        # --- 答案唯一性检查确实生效：重复候选被丢弃 ---
        reasons = drop_counts(gen["dropped"])
        check(reasons.get(("time", "extremum_second_not_unique"), 0) >= 2, reasons)
        check(reasons.get(("content", "content_prefix_or_second_not_unique"), 0) >= 2, reasons)
        check(reasons.get(("cite", "obs_ids_not_single"), 0) >= 1, reasons)
        print(f"唯一性检查 OK: 同一秒重复的 key 被拒 "
              f"{reasons[('time', 'extremum_second_not_unique')]} 道、"
              f"内容前缀重复被拒 {reasons[('content', 'content_prefix_or_second_not_unique')]} 道、"
              f"obs_ids 非单条被拒 {reasons[('cite', 'obs_ids_not_single')]} 道")

        # --- 同题型内答案不重复（重复答案的题已被丢弃）---
        for kind in KINDS:
            answers = [q["answer"] for q in qs if q["kind"] == kind]
            check(len(answers) == len(set(answers)), (kind, answers))
        print("答案去重 OK: 每个题型内答案互不相同")

        # --- 题量超过候选池时按池子封顶，不报错 ---
        check(0 < len(whole) <= 100, len(whole))
        print(f"候选池封顶 OK: 请求 100 道，实际生成 {len(whole)} 道")

        # --- 只读连接写不进去 ---
        write_refused = False
        try:
            conn.execute("INSERT INTO state(key,value) VALUES('x','1')")
        except sqlite3.OperationalError:
            write_refused = True
        check(write_refused, "只读连接必须拒绝写入")
        conn.close()
        print("只读连接 OK: 写入被 query_only 拒绝")

        # --- run_eval 端到端：退出码 0、报告含 D2 两个引用指标与 B1 两个数 ---
        json_path = Path(td) / "report.json"
        rc, text, err = run_main(["--db", str(db), "--n", "8", "--json", str(json_path)])
        check(rc == 0, (rc, err))
        for needle in ("本报告只测尺子，不测理解", "按题型分组的准确率与基线分数",
                       "引用正确率（D2 逐条）", "证据存在率（按题）",
                       "B1 全体", "B1 语义子集（剔除 cite 直取题）",
                       "PRAGMA query_only", "因缺字段而无法生成的题型"):
            check(needle in text, ("报告缺要素", needle))
        for name in run_eval.BASELINES:
            check(name in text, ("报告缺基线", name))
        print("run_eval OK: 退出码 0，报告含分题型准确率 / 引用正确率(D2) / 证据存在率 / "
              "三条基线 / B1 全体与语义子集 / 缺字段清单")

        data = json.loads(json_path.read_text(encoding="utf-8"))
        overall = data["evaluation"]["overall"]
        subset = data["evaluation"]["b1_semantic_subset"]
        check(len(data["questions"]) == 8 and overall["n"] == 8, overall["n"])
        check(data["unavailable"], "缺字段清单不能为空")
        check(overall["evidence_total"] >= overall["n"], overall["evidence_total"])
        check(overall["evidence_passed"] == overall["evidence_total"],
              ("真实题集的引用必须全过", overall))
        cite_n = sum(1 for q in data["questions"] if q["kind"] == "cite")
        cite_hit = data["evaluation"]["per_kind"]["cite"][run_eval.BASELINES[1]]
        check(subset["n"] == overall["n"] - cite_n and subset["excluded_kind"] == "cite",
              ("语料子集口径不符", subset, cite_n))
        # 报告论断“cite 题在查表下必得 100%”：换成能真失败的那一条，
        # 不用 evaluate 里 subset 的构造恒等式（那条永远为真，没有判别力）。
        check(cite_hit == cite_n, ("cite 题在 B1 下必须全对（报告论断可证伪）",
                                   cite_hit, cite_n))
        check(overall["evidence_exists"] == overall["n"], "证据存在率必须全过")
        print(f"JSON 报告 OK: {len(data['questions'])} 道题，引用 "
              f"{overall['evidence_passed']}/{overall['evidence_total']}，"
              f"B1 全体 {overall[run_eval.BASELINES[1]]}/{overall['n']}、"
              f"B1 语义子集 {subset['hit']}/{subset['n']}")

        # --- N4：报告文案按 data_version 的实际比对结果分支，不是无条件“一致” ---
        consistent = run_eval.render_markdown(data)
        data["meta"]["readonly"] = {"before": 7, "after": 9, "consistent": False}
        inconsistent = run_eval.render_markdown(data)
        check("（一致）" in consistent, "一致时必须写一致")
        check("（不一致）" in inconsistent, "不一致时必须写不一致")
        check("（一致）" not in inconsistent, "不一致时不得再宣称一致")
        print("data_version 文案分支 OK: 一致 / 不一致 两种都按实际结果输出")

        # --- 退出码 2：库不存在；库缺表 ---
        rc, _, err = run_main(["--db", str(Path(td) / "nope.db")])
        check(rc == 2, (rc, err))
        no_tables = Path(td) / "no_tables.db"
        sqlite3.connect(no_tables).close()
        rc, _, err = run_main(["--db", str(no_tables)])
        check(rc == 2 and "库不可用" in err, (rc, err))
        print("退出码 2 OK: 库不存在 / 库缺表")

        # --- 退出码 4：空池（表在但 0 行）---
        empty = Path(td) / "empty.db"
        Store(empty).close()
        rc, out, err = run_main(["--db", str(empty)])
        check(rc == 4 and "空池" in err, (rc, err))
        check(out == "", "空池不应打印报告")
        print("退出码 4 OK: 空池（生成 0 道题）")

        # --- 退出码 5：尺子自检失败（复算判不过）---
        original = run_eval.verify_question
        run_eval.verify_question = lambda conn, q: {
            "recalc_ok": False, "evidence_passed": 0,
            "evidence_total": len(q["evidence"]), "evidence_exists": True}
        try:
            rc, _, err = run_main(["--db", str(db), "--n", "8"])
        finally:
            run_eval.verify_question = original
        check(rc == 5 and "尺子自检失败" in err, (rc, err))
        print("退出码 5 OK: 尺子自检失败")

        # --- 退出码 6：参数误用（--n 小于 1），不得报成“库不可用于评测” ---
        for bad in ("0", "-3"):
            rc, out, err = run_main(["--db", str(db), "--n", bad])
            check(rc == 6, (rc, err))
            check("参数 --n 必须 ≥1" in err, ("参数误用缺专属文案", bad, err))
            check("库不可用于评测" not in err, ("参数误用被报成库缺陷", bad, err))
            check(out == "", ("参数误用不应打印报告", out))
        print("退出码 6 OK: --n 0 / -3 报参数误用（专属文案），不报库缺陷")

        # --- 退出码 6 的拦截点：--n 误用必须先于打开/检查库 ---
        check_exit6_refuses_before_open(Path(td))

        # --- v1!=v0：判定先于打印，报告作废且 stdout 为空 ---
        check_invalidated_report(db)

        # --- 只读不写：主库字节不变、WAL 无写入帧 ---
        check(db_digest(db) == before_digest, "评测改写了主库文件")
        check(before_wal == 0 and wal_payload(db) == 0, "WAL 里出现了写入帧")
        print("只读不写 OK: 主库字节不变、WAL 无写入帧")

    print(f"\nWP-EVAL-02 smoke: ALL PASS（断言 {CHECKS} 条）")


if __name__ == "__main__":
    main()
