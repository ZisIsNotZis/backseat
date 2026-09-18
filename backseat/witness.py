"""见证层（L1）：像素→文字的唯一转换点。纯事实，不评价（DESIGN §10.G4）。

- anchor/diff 双模式：无锚/锚龄超限/锚距超限 → anchor（描述整体场景），否则 diff
- context 组装（DESIGN §7）：冻结前缀（system+schema+USER_MODEL+BIGS+MIDS+SMALLS）
  → KB命中行 → expressions尾部 → 本轮（时钟放最末，≤1 图）
- 严格 JSON 输出契约；解析失败重试 1 次后丢弃+日志（链路 B）
- 逐调用计量：metrics 表 + Trajectory JSONL
"""

from __future__ import annotations

import json
import sqlite3
import time

from .clock import Stamp, fmt_wall
from .model import ModelClient, ModelError, image_message
from .trajectory import Trajectory

# 冻结 system（含固定记忆引擎说明段——缓存安全：逐字不变）
SYSTEM_PROMPT = """你是见证层：把屏幕帧压缩为纯事实记录。只输出一个 JSON 对象，无其他文字。

输出契约（严格遵守）：
{"type":"anchor"或"diff", "describe":str, "diff":str, "intent":str,
 "candidate_project":str?, "importance":0.0~1.0?}
- mode=anchor 时填 describe（整体场景），不填 diff；mode=diff 时填 diff（相对上次的变化），不填 describe
- intent：行为动词短语（"在做什么"）
- 认知边界：深潜前不定业务性质，只说证据支持的话（"画像里没有的项目，业务待确认"）
- importance：事件重要度 0~1；新项目首见给 candidate_project
- 隐私：转写文字中出现密码/密钥/凭证等敏感内容时以▇代替
- 纯事实：不评价、不吐槽、无 roast

记忆引擎说明（固定）：你会看到 USER_MODEL（你对用户的当前理解，知识·名词）、
MEDIUM/BIG（历史纪要，历史·动词）、[KB命中行]（相关的长期知识自动浮现）、
SMALLS（事实流水）——均由记忆引擎维护注入，不是你生成的。"""


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
        self.stats = {"calls": 0, "parse_fail": 0, "discarded": 0, "anchors": 0}
        self._system = SYSTEM_PROMPT

    # —— context 组装（顺序即缓存契约；时钟最末）——
    def _context(self, obs_row) -> tuple[list[dict], dict]:
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
        mode = "anchor" if (not anchor or n_since >= self.cfg.anchor_every_n
                            or age_min >= self.cfg.anchor_age_min) else "diff"
        turn_text = json.dumps({
            "now": fmt_wall(obs_row["ts_wall"]),
            "mode": mode,
        }, ensure_ascii=False)
        messages = [{"role": "system", "content": ctx},
                    image_message(turn_text, obs_row["ref"])]
        return messages, {"mode": mode, "n_since": n_since, "age_min": age_min}

    def _kb_hits(self) -> list[str]:
        """简易唤起：kb.keys 命中近期 small 内容（完整机制 M4：last_shown 防重等）。"""
        recent = " ".join(r["content"] or "" for r in self.store.session_stream(limit=5))
        if not recent:
            return []
        hits = []
        for row in self.store.conn.execute(
                "SELECT id,keys,desc FROM kb WHERE open=1"):
            for k in json.loads(row["keys"]):
                if k and k in recent:
                    hits.append(f"({row['id']}) {row['desc']}")
                    break
        return hits

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
        payload = {}
        if mini.get("importance") is not None:
            payload["importance"] = mini["importance"]
        if mini.get("candidate_project"):
            payload["candidate_project"] = mini["candidate_project"]
        mid = self.store.insert(
            "messages", level="small",
            ts_start=obs_row["ts_wall"], ts_end=obs_row["ts_wall"],
            content=mini.get("describe") or mini.get("diff"),
            intent=mini.get("intent") or None,
            type=mini["type"], source="vlm", model=self.model.model,
            payload=payload or None,
            obs_ids=json.dumps([obs_row["id"]]))
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
                 f"{(mini.get('describe') or mini.get('diff'))[:60]}")
        return mid

    # —— 游标驱动：处理所有未见证的观察 ——
    def process_pending(self, batch: int = 4) -> int:
        cursor = self.store.get_state("witness_cursor", 0)
        rows = self.store.conn.execute(
            "SELECT * FROM observations WHERE id>? AND kind='screen.notable' "
            "ORDER BY id LIMIT ?", (cursor, batch)).fetchall()
        n = 0
        for r in rows:
            try:
                self.process(r)  # 丢弃（链路 B 终点）也推进游标
            except ModelError as e:
                self.stats["errors"] = self.stats.get("errors", 0) + 1
                self.log(f"[witness] 模型故障，游标不推进（下轮重试）：{e}")
                break  # 模型不可用时停止本批，避免连续打失败调用
            self.store.set_state("witness_cursor", r["id"])
            n += 1
        return n
