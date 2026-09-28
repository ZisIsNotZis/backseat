"""压缩层（L2）：纯文字压缩，纯事实。汉诺塔：K条small→1 mid，M条mid→1 big。

- 触发：未折叠子消息数达阈值（K=chunk_k / M=epoch_m）；gap 强制闭合属引擎调度，此处不涉
- 输出严格 JSON：{content, keys[], callback_tags?[], events?[], user_model_delta{}}
- 链路 D：坏输出重试 1 次后跳过（不折叠，下轮重试）
- 合并 = 子消息 folded=1 + parent_id（行不删，可下钻）；USER_MODEL ⊕ delta
"""

from __future__ import annotations

import json
import time

from .clock import Stamp
from .model import ModelClient, ModelError
from .trajectory import Trajectory

SYSTEM_PROMPT = """你是记忆压缩层：把若干条事实流水合并为一条更高层纪要。只输出一个 JSON 对象。
输出契约：
{"content":"时段+场景+意图弧线+关键事件的叙事",
 "keys":["唤起词",…],
 "callback_tags":["可回扣的梗/事件标签",…],
 "events":["关键事件短语",…],
 "user_model_delta":{"对用户认知的增量":…}}
- 纯事实；keys 选后续提及能唤起这段记忆的词（项目名/工具/主题）
- user_model_delta 只写确实新学到的东西，无则 {}"""


def parse_merge(text: str) -> dict | None:
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        if t.startswith("json"):
            t = t[4:]
    try:
        d = json.loads(t)
    except json.JSONDecodeError:
        return None
    if not isinstance(d, dict) or not isinstance(d.get("content"), str) or not d["content"]:
        return None
    if not isinstance(d.get("keys"), list) or not d["keys"]:
        return None
    return d


class Compressor:
    def __init__(self, store, model: ModelClient, trajectory: Trajectory,
                 cfg, log=print) -> None:
        self.store = store
        self.model = model
        self.trajectory = trajectory
        self.cfg = cfg
        self.log = log
        from .kb import KB
        self.kb = KB(store)
        self.stats = {"merges": 0, "parse_fail": 0, "skipped": 0}

    def _unfolded_count(self, level: str) -> int:
        return self.store.conn.execute(
            "SELECT count(*) c FROM messages WHERE level=? AND folded=0",
            (level,)).fetchone()["c"]

    def _candidates(self, level: str, n: int):
        return self.store.conn.execute(
            "SELECT * FROM messages WHERE level=? AND folded=0 "
            "ORDER BY ts_start LIMIT ?", (level, n)).fetchall()

    def maybe_merge(self, from_level: str) -> int | None:
        """达到阈值则合并一层。返回新消息 id 或 None。"""
        to_level = {"small": "medium", "medium": "big"}[from_level]
        k = self.cfg.chunk_k if from_level == "small" else self.cfg.epoch_m
        if self._unfolded_count(from_level) < k:
            return None
        rows = self._candidates(from_level, k)
        body = "\n".join(
            f"[{r['ts_start']:.0f}] ({r['type'] or from_level}) {r['content']}"
            f"（intent: {r['intent']}）" if r["intent"] else
            f"[{r['ts_start']:.0f}] ({r['type'] or from_level}) {r['content']}"
            for r in rows)
        messages = [{"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": f"合并以下 {len(rows)} 条{from_level}：\n{body}"}]
        merged = None
        for attempt in (1, 2):  # 链路 D：重试 1 次
            text, usage = self.model.chat(messages)
            self.trajectory.record(f"compress:{to_level}", self.model.model,
                                   messages, text, usage)
            self.store.insert("metrics", ts_wall=Stamp.now().wall, ts_mono=Stamp.now().mono,
                              purpose=f"compress:{to_level}", model=self.model.model,
                              tokens_in=usage.get("in", 0), tokens_out=usage.get("out", 0),
                              cached_tokens=usage.get("cached", 0),
                              latency_ms=usage.get("latency_ms", 0))
            merged = parse_merge(text)
            if merged is not None:
                break
            self.stats["parse_fail"] += 1
        if merged is None:
            self.stats["skipped"] += 1
            self.log(f"[compress] {from_level}→{to_level} 两次解析失败，跳过（不折叠）")
            return None

        delta = merged.get("user_model_delta")
        parent = self.store.insert(
            "messages", level=to_level,
            ts_start=rows[0]["ts_start"], ts_end=rows[-1]["ts_end"],
            content=merged["content"], keys=json.dumps(merged["keys"], ensure_ascii=False),
            payload={k2: merged[k2] for k2 in ("callback_tags", "events")
                     if merged.get(k2)} or None,
            user_model_delta=delta or None)
        for r in rows:
            self.store.conn.execute(
                "UPDATE messages SET folded=1, parent_id=? WHERE id=?", (parent, r["id"]))
        self.store.conn.commit()
        if delta and isinstance(delta, dict):
            um = self.store.get_state("user_model", {})
            for kk, vv in delta.items():  # 浅合并；冲突时 mid 的较新值胜
                um[kk] = vv
            self.store.set_state("user_model", um)
        if hasattr(self, "kb"):
            self.kb.clear_last_shown()  # 合并边界：清零防漏（DESIGN §3 唤起机制）
        self.stats["merges"] += 1
        self.log(f"[compress] {len(rows)}×{from_level} → {to_level}#{parent} "
                 f"keys={merged['keys']}")
        return parent
