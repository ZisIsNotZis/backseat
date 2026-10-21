"""见证层（L1）：像素→文字的唯一转换点。纯事实，不评价（DESIGN §10.G4）。

- anchor/diff 双模式：无锚/锚龄超限/锚距超限 → anchor（描述整体场景），否则 diff
- context 组装（DESIGN §7）：冻结前缀（system+schema+USER_MODEL+BIGS+MIDS+SMALLS）
  → KB命中行 → expressions尾部 → 本轮（时钟放最末，≤1 图）
- 严格 JSON 输出契约；解析失败重试 1 次后丢弃+日志（链路 B）
- 逐调用计量：metrics 表 + Trajectory JSONL

证据基（SCHEMA §语义层证据引用）：一行写下的每个可核对断言都要在自己的 obs_ids
里找到依据，所以本模块写行时做三件事，而不是只挂本轮帧：
1. 窗口取本行 **被引用观察** 的 min/max（不是 rows[0]/rows[-1] 的 ts_wall——id 与
   ts_wall 不同序，按 id 取的批首批尾会得到倒置窗口与落在窗外的引用）；
2. `从X切至Y` 切换句把被切离的那一端（基帧）并进本行 obs_ids：本行自带过渡的两端；
3. 通行称呼（`浅色外套男子`）只认本轮点名了的那张图，点不出来就拒绝，不静默沿用上一行。
"""

from __future__ import annotations

import base64
import json
import re
import sqlite3
import time

from .clock import Stamp, fmt_wall
from .kb import KB
from .model import ModelClient, ModelError, image_message
from .trajectory import Trajectory
# 冻结 system（含固定记忆引擎说明段——缓存安全：逐字不变）
SYSTEM_PROMPT = """你是见证层：把屏幕帧压缩为纯事实记录。只输出一个 JSON 对象，无其他文字。

输出契约（严格遵守）：
{"type":"anchor"或"diff", "describe":str, "diff":str, "intent":str,
 "candidate_project":str?, "importance":0.0~1.0?,
 "since":str?, "cast":[{"name":str,"frame":int}]?}
- mode=anchor 时填 describe（整体场景），不填 diff；mode=diff 时填 diff（相对上次的变化），不填 describe
- intent：行为动词短语（"在做什么"）
- 认知边界：深潜前不定业务性质，只说证据支持的话（"画像里没有的项目，业务待确认"）
- importance：事件重要度 0~1；新项目首见给 candidate_project
- 隐私：转写文字中出现密码/密钥/凭证等敏感内容时以▇代替
- 纯事实：不评价、不吐槽、无 roast
- 对比句（"从X切至Y""相较上一帧""仍为"）：必须同时填 since＝你对比的那一帧的时间，
  照抄本轮"图N（…）"或 SMALLs 行首的"月-日 时:分:秒"；本行会把那一帧一并记为证据。
  你对比的是哪一帧就写哪一帧，不要因为它不在本轮图里就省掉 since
- 人物/物体的通行称呼（如"浅色外套男子"）：填 cast=[{"name":称呼原文,"frame":图号}]，
  图号是本轮第几张图（从 1 起）；本帧看不到就不写这个称呼，也不沿用上一行的说法

记忆引擎说明（固定）：你会看到 USER_MODEL（你对用户的当前理解，知识·名词）、
MEDIUM/BIG（历史纪要，历史·动词）、[KB命中行]（相关的长期知识自动浮现）、
SMALLS（事实流水）——均由记忆引擎维护注入，不是你生成的。"""


# 切换句：一行自称状态发生了过渡，过渡的被切离端必须同为该行的证据
SWITCH_RE = re.compile(r"从[^，。；,;\s]{1,24}?切至")
# 模型回填的 since 只可能是我们自己印出来的时间格式（fmt_wall / 图N（...））
STAMP_RE = re.compile(r"(?:(\d{1,2})-(\d{1,2})\s+)?(\d{1,2}):(\d{2}):(\d{2})")
# since 指名的帧与观察 ts_wall 的容差：印出来的时间截断到秒，故下界 -1s、上界 +2s
SINCE_SLACK = 2.0


# 人物通行称呼的名词尾：`浅色外套男子` 这类站立描述里唯一机械可识别的部分。
# 名词前 2~6 个汉字连同名词一起进册，并保留后缀变体（`外套男子` ⊂ `浅色外套男子`），
# 这样上一行写"穿浅色外套的男子"、本行写"浅色外套男子"仍能被认成同一个称呼。
PERSON_NOUNS = ("男子", "女子", "男人", "女人", "男孩", "女孩", "男生", "女生", "小孩")
PERSON_RE = re.compile("|".join(PERSON_NOUNS))
EPITHET_PREFIX = (2, 6)


def epithet_forms(text: str) -> list[str]:
    """内容里的人物通行称呼候选（长→短，含后缀变体）。没有就是空集，不猜。"""
    forms: set[str] = set()
    for m in PERSON_RE.finditer(text):
        run = re.search(r"[\u4e00-\u9fff]+$", text[:m.start()])
        chars = (run.group(0) if run else "").replace("的", "")  # "穿浅色外套的男子" ≡ "浅色外套男子"
        for k in range(EPITHET_PREFIX[0], EPITHET_PREFIX[1] + 1):
            if len(chars) >= k:
                forms.add(chars[-k:] + m.group(0))
    return sorted(forms, key=len, reverse=True)


def parse_stamp(text, ref_wall: float) -> float | None:
    """把模型回填的时间解析回墙钟秒；解析不出返回 None（不猜、不默认）。

    接受 `%m-%d %H:%M:%S`（fmt_wall）与 `%H:%M:%S`（当天）；跨年按 ref_wall 归位。
    """
    if not isinstance(text, str):
        return None
    m = STAMP_RE.search(text)
    if not m:
        return None
    mon, day, hh, mm, ss = m.groups()
    lt = time.localtime(ref_wall)
    mon, day = (int(mon), int(day)) if mon else (lt.tm_mon, lt.tm_mday)
    for year in (lt.tm_year, lt.tm_year - 1):  # 12-31 23:59 之后回指 01-01 属去年
        try:
            ts = time.mktime((year, mon, day, int(hh), int(mm), int(ss), 0, 0, -1))
        except (ValueError, OverflowError):
            return None
        if ts <= ref_wall + 86400:
            return ts
    return None


def parse_ids(raw) -> list[int]:
    """messages.obs_ids 的 json 文本 → id 列表；坏值当空（缺值就是缺值）。"""
    try:
        return [int(x) for x in json.loads(raw)] if raw else []
    except (ValueError, TypeError):
        return []


def parse_mini(text: str) -> dict | None:
    """严格解析见证层输出。非法返回 None（由调用方决定重试/丢弃）。"""
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        if t.startswith("json"):
            t = t[4:]
    try:
        d = json.loads(t)
    except json.JSONDecodeError:
        return None
    if not isinstance(d, dict) or d.get("type") not in ("anchor", "diff"):
        return None
    body = d.get("describe") if d["type"] == "anchor" else d.get("diff")
    if not isinstance(body, str) or not body or not isinstance(d.get("intent", ""), str):
        return None
    imp = d.get("importance")
    if imp is not None and not (isinstance(imp, (int, float)) and 0.0 <= imp <= 1.0):
        return None
    return d


class Witness:
    def __init__(self, store: sqlite3.Connection | "object", model: ModelClient,
                 trajectory: Trajectory, cfg, log=print) -> None:
        self.store = store        # backseat.store.Store
        self.model = model
        self.trajectory = trajectory
        self.cfg = cfg
        self.log = log
        self.kb = KB(store)
        self._err_log_at: dict[str, float] = {}
        self.stats = {"calls": 0, "parse_fail": 0, "discarded": 0, "anchors": 0}
        self._system = SYSTEM_PROMPT

    def _needs_anchor(self, ts_wall: float) -> bool:
        a = self.store.get_state("anchor", {})
        if not a:
            return True
        return (a.get("n", 0) >= self.cfg.anchor_every_n
                or (ts_wall - a.get("ts_wall", 0)) / 60.0 >= self.cfg.anchor_age_min)

    # —— context 组装（顺序即缓存契约；时钟最末）——
    def _context(self, obs_row, batch: int = 1) -> tuple[list[dict], dict]:
        st = self.store
        user_model = st.get_state("user_model", {})
        bigs = st.conn.execute(
            "SELECT content,ts_end FROM messages WHERE level='big' AND folded=0 "
            "ORDER BY ts_end DESC LIMIT 5").fetchall()
        mids = st.conn.execute(
            "SELECT content,ts_end FROM messages WHERE level='medium' AND folded=0 "
            "ORDER BY ts_end DESC LIMIT 10").fetchall()
        smalls = st.session_stream(limit=30)
        kb_hits = self._kb_hits()
        exprs = st.conn.execute(
            "SELECT kind,content FROM expressions ORDER BY ts_wall DESC LIMIT 5").fetchall()

        def sec(title, rows, fmt=lambda r: str(r["content"])):
            if not rows:
                return ""
            return f"[{title}]\n" + "\n".join(fmt(r) for r in rows) + "\n"

        ctx = self._system + "\n"
        ctx += f"[USER_MODEL]\n{json.dumps(user_model, ensure_ascii=False)}\n"
        ctx += sec("BIGs", bigs) + sec("MEDIUMs", mids)
        ctx += sec("SMALLs", smalls, lambda r: f"{fmt_wall(r['ts_start'])} {r['content']}")
        if kb_hits:
            ctx += "[KB命中行]\n" + "\n".join(kb_hits) + "\n"
        ctx += sec("expressions尾部", exprs,
                   lambda r: f"({r['kind']}) {r['content']}")

        anchor = st.get_state("anchor", {})
        n_since = anchor.get("n", 0)
        age_min = (obs_row["ts_wall"] - anchor.get("ts_wall", 0)) / 60.0
        mode = "anchor" if self._needs_anchor(obs_row["ts_wall"]) else "diff"
        turn_text = json.dumps({
            "now": fmt_wall(obs_row["ts_wall"]),
            "mode": mode,
        }, ensure_ascii=False)
        messages = [{"role": "system", "content": ctx},
                    image_message(turn_text, obs_row["ref"])]
        return messages, {"mode": mode, "n_since": n_since, "age_min": age_min}

    def _log_err(self, msg: str) -> None:
        """同类错误 5 分钟最多记一次（防刷屏毁日志）。"""
        key = msg[:80]
        now = time.time()
        if now - self._err_log_at.get(key, 0) > 300:
            self._err_log_at[key] = now
            self.log(msg)

    def _kb_hits(self) -> list[str]:
        """唤起：KB.keys 命中近期 small 内容；!always 周期重放；!at 到点（KB 类负责）。"""
        recent = " ".join(r["content"] or "" for r in self.store.session_stream(limit=5))
        if not recent:
            return self.kb.hits_for("")
        return self.kb.hits_for(recent)

    # —— 证据基构造：窗口 / 切换句基帧 / 通行称呼 ——
    def _window_of(self, ids: list[int], fallback: float) -> tuple[float, float]:
        """本行窗口 = 被引用观察 ts_wall 的 min/max（而非批首批尾的 ts_wall）。

        id 与 ts_wall 不同序（抓帧多路并发、补写落库），按 id 取头部尾部的 ts_wall
        会得到倒置窗口（ts_start > ts_end）和落在自窗外的 obs_ids 成员。窗口取
        被引用集合的极值，则“成员必在自窗内”是构造出来的，不是事后巧合。
        """
        if not ids:
            return fallback, fallback
        qs = ",".join("?" * len(ids))
        rows = self.store.conn.execute(
            f"SELECT ts_wall FROM observations WHERE id IN ({qs})", tuple(ids)).fetchall()
        walls = [r["ts_wall"] for r in rows]
        if not walls:
            return fallback, fallback
        return min(walls), max(walls)

    def _switch_base(self, mini: dict, content: str, ref_wall: float) -> list[int]:
        """切换句的被切离端：模型点名的 since 帧，缺省取上一条 small 的 obs_ids。

        只有内容确实写了 `从X切至Y` 才加基帧——没有过渡句就没有第二个端点，不加，
        以免把“上一条”无差别塞进每一行。基帧通常早于本轮帧，故本行窗口随之向后延伸
        （窗口取自被引用集合的 min/max，不破坏“成员在窗内”）。
        """
        if not SWITCH_RE.search(content):
            return []
        ts = parse_stamp(mini.get("since"), ref_wall)
        if ts is not None:
            row = self.store.conn.execute(
                "SELECT id FROM observations WHERE ref IS NOT NULL "
                "AND ts_wall BETWEEN ? AND ? ORDER BY abs(ts_wall-?) LIMIT 1",
                (ts - 1.0, ts + SINCE_SLACK, ts)).fetchone()
            if row:
                return [int(row["id"])]
        prev = self.store.conn.execute(
            "SELECT obs_ids FROM messages WHERE level='small' AND obs_ids IS NOT NULL "
            "ORDER BY id DESC LIMIT 1").fetchone()
        return parse_ids(prev["obs_ids"]) if prev else []

    def _settle_epithets(self, content: str, mini: dict,
                         n_frames: int) -> tuple[str | None, list[str]]:
        """通行称呼必须点名本轮某张图，点不出来就拒绝。

        `浅色外套男子` 这类站立描述只允许来自本行引用的帧。能留下的情形只有三种：
        cast 里点名了本轮第 n 张图（1≤n≤本轮图数）；本轮首次出现（上一行没用过，
        故它只可能来自本帧）；本轮一张图都没有时——那就没有任何情形，全部拒绝。

        上一行用过（state["epithets"] 记着）而本行又写出来的称呼，本模块无从复核像素，
        所以拒绝而不是静默沿用：正文里删掉它、payload 记 epithet_refused、stats 计数、
        日志留一行。删空的整行不落库（落一行空话比不落更糟）。

        机械看得到的只有名词尾（男子/女子/…），所以登记与比对都以名词前的后缀变体为准：
        模型不填 cast 时机制照样生效，代价是同一人反复出现时称呼会被反复拒绝。
        返回 (content 或 None, 被拒称呼)。
        """
        known = [e for e in self.store.get_state("epithets", []) if isinstance(e, str)]
        attributed, declared = [], []
        cast = mini.get("cast")
        for item in cast if isinstance(cast, list) else []:
            if not isinstance(item, dict):
                continue
            name, frame = item.get("name"), item.get("frame")
            if not isinstance(name, str) or not name:
                continue
            declared.append(name)
            if isinstance(frame, int) and 1 <= frame <= n_frames:
                attributed.append(name)

        def attributed_ok(form: str) -> bool:
            return any(form in n or n in form for n in attributed)

        refused, out = [], content
        if n_frames <= 0:  # 无帧可看：任何人物称呼都没有依据
            for form in epithet_forms(out):
                if form in out:
                    out = out.replace(form, "")
                    refused.append(form)
        else:
            for form in sorted(known, key=len, reverse=True):
                if form in out and not attributed_ok(form):
                    out = out.replace(form, "")
                    refused.append(form)
        if refused:
            out = re.sub(r"[，、,]{2,}", "，", out)
            out = re.sub(r"^[，、,。;；\s]+", "", out).strip()
            out = re.sub(r"[，、,;；\s]+$", "", out)
            self.stats["epithet_refused"] = self.stats.get("epithet_refused", 0) + len(refused)
            self._log_err(f"[witness] 拒绝越帧称呼 {refused}（本轮未点名任何一图）")
        kept = [n for n in declared if n in out] + epithet_forms(out)
        self.store.set_state("epithets", sorted(set(kept)))
        if not out.strip():
            return None, refused
        return out, refused

    def _row_basis(self, mini: dict, content: str, frame_ids: list[int],
                   ref_wall: float, n_frames: int):
        """一行落库前的证据基：称呼去留 → 切换句基帧并入 → 窗口取引用集合极值。

        返回 (content|None, obs_ids, ts_start, ts_end, 基帧, 被拒称呼)；content 为 None
        表示整行拒绝（只剩被拒称呼与计数），调用方丢弃本行。
        """
        raw = content  # 切换句按模型写下的原话判：称呼被删掉也不该让过渡句消失
        content, refused = self._settle_epithets(content, mini, n_frames)
        if content is None:
            return None, [], ref_wall, ref_wall, [], refused
        base = self._switch_base(mini, raw, ref_wall)
        ids = list(dict.fromkeys([*frame_ids, *base]))
        ts_start, ts_end = self._window_of(ids, ref_wall)
        return content, ids, ts_start, ts_end, base, refused

    @staticmethod
    def _payload(mini: dict, base: list[int], refused: list[str]) -> dict:
        payload = {}
        if mini.get("importance") is not None:
            payload["importance"] = mini["importance"]
        if mini.get("candidate_project"):
            payload["candidate_project"] = mini["candidate_project"]
        if base:
            payload["switch_base"] = base  # 过渡句的另一端：已入 obs_ids，此处只记明身份
        if refused:
            payload["epithet_refused"] = refused  # 被拒称呼：失败可见，不静默沿用
        return payload

    # —— 主入口：一条观察 → 一条 small 消息 ——
    def process(self, obs_row) -> int | None:
        """返回 small 消息 id；丢弃（解析失败）返回 None；模型故障抛 ModelError。"""
        messages, minfo = self._context(obs_row)
        mini = None
        for attempt in (1, 2):  # 链路 B：解析失败重试 1 次后丢弃
            text, usage = self.model.chat(messages)
            self.stats["calls"] += 1
            self.trajectory.record(f"witness:{minfo['mode']}", self.model.model,
                                   messages, text, usage)
            self.store.insert("metrics", ts_wall=Stamp.now().wall,
                              ts_mono=Stamp.now().mono,
                              purpose=f"witness:{minfo['mode']}", model=self.model.model,
                              tokens_in=usage.get("in", 0), tokens_out=usage.get("out", 0),
                              cached_tokens=usage.get("cached", 0),
                              latency_ms=usage.get("latency_ms", 0))
            mini = parse_mini(text)
            if mini is not None:
                break
            self.stats["parse_fail"] += 1
            self.log(f"[witness] 解析失败(第{attempt}次)：{text[:120]!r}")
        if mini is None:
            self.stats["discarded"] += 1
            return None  # 链路 B 终点：丢弃 + 已日志

        is_anchor = mini["type"] == "anchor"
        content, ids, ts_start, ts_end, base, refused = self._row_basis(
            mini, mini.get("describe") or mini.get("diff") or "",
            [int(obs_row["id"])], obs_row["ts_wall"], 1 if obs_row["ref"] else 0)
        if content is None:
            self.stats["discarded"] += 1
            self.log(f"[witness] 拒绝落行（称呼删空）：{fmt_wall(obs_row['ts_wall'])} "
                     f"refused={refused}")
            return None
        payload = self._payload(mini, base, refused)
        mid = self.store.insert(
            "messages", level="small",
            ts_start=ts_start, ts_end=ts_end,
            content=content,
            intent=mini.get("intent") or None,
            type=mini["type"], source="vlm", model=self.model.model,
            payload=payload or None,
            obs_ids=json.dumps(ids))
        # 锚状态：anchor 重置计数与锚龄；diff 累加
        anchor = self.store.get_state("anchor", {})
        self.store.set_state("anchor", {
            "ts_wall": obs_row["ts_wall"] if is_anchor else
            anchor.get("ts_wall", obs_row["ts_wall"]),
            "n": 0 if is_anchor else anchor.get("n", 0) + 1,
        })
        if is_anchor:
            self.stats["anchors"] += 1
        self.log(f"[witness] {mini['type']} #{mid} ({fmt_wall(obs_row['ts_wall'])}) "
                 f"{content[:60]}")
        return mid

    # —— 批帧：一次调用 ≤6 张图，输出同 schema 的单个 mini（DESIGN §7.5 同批压缩）——
    def process_batch(self, rows) -> int | None:
        if not rows:
            return None
        messages, minfo = self._context(rows[0], batch=len(rows))
        turn = json.loads(messages[-1]["content"][0]["text"])
        turn["frames"] = [fmt_wall(r["ts_wall"]) for r in rows]
        messages[-1]["content"][0]["text"] = json.dumps(turn, ensure_ascii=False)
        for i, r in enumerate(rows):
            if not r["ref"]:
                continue  # 无帧载荷的观察只贡献时间戳
            b64 = base64.b64encode(open(r["ref"], "rb").read()).decode()
            messages[-1]["content"].append(
                {"type": "text", "text": f"图{i + 1}（{fmt_wall(r['ts_wall'])}）"})
            messages[-1]["content"].append(
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
        text, usage = self.model.chat(messages)
        self.stats["calls"] += 1
        self.trajectory.record(f"witness:batch{len(rows)}", self.model.model,
                               messages, text, usage)
        self.store.insert("metrics", ts_wall=Stamp.now().wall, ts_mono=Stamp.now().mono,
                          purpose=f"witness:batch{len(rows)}", model=self.model.model,
                          tokens_in=usage.get("in", 0), tokens_out=usage.get("out", 0),
                          cached_tokens=usage.get("cached", 0),
                          latency_ms=usage.get("latency_ms", 0))
        mini = parse_mini(text)
        if mini is None:
            self.stats["parse_fail"] += 1
            self.stats["discarded"] += len(rows)
            self.log(f"[witness] 批解析失败丢弃 {len(rows)} 帧：{text[:100]!r}")
            return None
        content, ids, ts_start, ts_end, base, refused = self._row_basis(
            mini, mini.get("describe") or mini.get("diff") or "",
            [int(r["id"]) for r in rows], rows[0]["ts_wall"],
            sum(1 for r in rows if r["ref"]))
        if content is None:
            self.stats["discarded"] += len(rows)
            self.log(f"[witness] 拒绝落行（称呼删空）丢弃 {len(rows)} 帧：refused={refused}")
            return None
        payload = self._payload(mini, base, refused)
        mid = self.store.insert(
            "messages", level="small",
            ts_start=ts_start, ts_end=ts_end,
            content=content,
            intent=mini.get("intent") or None,
            type=mini["type"], source="vlm", model=self.model.model,
            payload=payload or None, obs_ids=json.dumps(ids))
        anchor = self.store.get_state("anchor", {})
        self.store.set_state("anchor", {"ts_wall": anchor.get("ts_wall"),
                                        "n": anchor.get("n", 0) + len(rows)})
        return mid

    # —— 游标驱动：黑帧廉价跳过，锚须单帧，diff 拼批（≤batch_size 帧/调用）——
    def process_pending(self, batch_size: int = 6) -> int:
        cursor = self.store.get_state("witness_cursor", 0)
        rows = self.store.conn.execute(
            "SELECT * FROM observations WHERE id>? AND kind='screen.notable' "
            "ORDER BY id LIMIT 24", (cursor,)).fetchall()
        # 廉价预过滤：锁屏/黑帧（jpeg 近全黑 <40KB）不值得 LLM 调用
        work, last_id = [], cursor
        for r in rows:
            if (r["bytes"] or 0) < 40000:
                self.stats["blank_skipped"] = self.stats.get("blank_skipped", 0) + 1
                last_id = r["id"]
                continue
            work.append(r)
        n = 0
        i = 0
        while i < len(work):
            r = work[i]
            try:
                if self._needs_anchor(r["ts_wall"]):
                    self.process(r)
                    i += 1
                else:
                    group = work[i:i + batch_size]
                    self.process_batch(group)
                    i += len(group)
                n += 1
                last_id = work[i - 1]["id"]
                self.store.set_state("witness_cursor", last_id)
            except ModelError as e:
                self.stats["errors"] = self.stats.get("errors", 0) + 1
                time.sleep(30)  # 任何模型故障都退避；游标不推进，下轮重试
                self._log_err(f"[witness] 模型故障，游标不推进：{e}")
                break
        if last_id > cursor:
            self.store.set_state("witness_cursor", last_id)
        return n
