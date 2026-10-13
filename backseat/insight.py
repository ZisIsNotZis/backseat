"""洞察挖掘器（确定性，无 LLM）：找出"用户不知道而系统知道"的东西。

产品原则（用户 2026-10-05 拍板）：说话的唯一理由是信息不对称。系统独有的优势是
跨时间的眼睛——重复、间隔、累积、异常；屏幕上的内容用户自己看得见，复述无价值。

输出候选洞察供人格层引用；人格层据此说话（或沉默）。
"""

from __future__ import annotations

from .config import Config  # noqa: F401  (类型提示用)

import json
import time


class InsightMiner:
    def __init__(self, store, cfg) -> None:
        self.store = store
        self.cfg = cfg

    # —— 主题词表：KB 的 keys（系统自有概念）+ USER_MODEL 的 projects ——
    def _topics(self) -> list[str]:
        out = []
        for row in self.store.conn.execute(
                "SELECT keys FROM kb WHERE open=1 ORDER BY updated DESC LIMIT 20"):
            for k in json.loads(row["keys"])[:3]:
                if k and k not in out:
                    out.append(k)
        um = self.store.get_state("user_model", {})
        for k in (um.get("projects") or [])[:10]:
            if k and k not in out:
                out.append(k)
        return out[:12]

    def _topic_stats(self, topic: str) -> dict | None:
        like = f'%"{topic}"%'
        row = self.store.conn.execute(
            "SELECT count(*) c, count(DISTINCT date(ts_start,'unixepoch','localtime')) d,"
            " min(ts_start) f, max(ts_end) l, max(level) mx"
            " FROM messages WHERE parent_id IS NULL AND keys LIKE ?", (like,)).fetchone()
        if not row or not row["c"]:
            return None
        return {"mentions": row["c"], "days": row["d"], "first": row["f"], "last": row["l"]}

    def candidates(self, now: float | None = None, limit: int = 2) -> list[dict]:
        """返回 [{kind, detail, topic}]，按"信息不对称价值"排序。"""
        now = now or time.time()
        found: list[dict] = []
        for topic in self._topics():
            st = self._topic_stats(topic)
            if not st:
                continue
            gap_h = (now - st["last"]) / 3600.0
            if st["mentions"] >= 3:
                found.append({"kind": "repeat", "topic": topic,
                              "detail": f"「{topic}」在记忆里出现第 {st['mentions']} 次"
                                        f"（横跨 {st['days']} 天）"})
            if st["mentions"] >= 2 and gap_h >= 24:
                found.append({"kind": "revival", "topic": topic,
                              "detail": f"「{topic}」上次出现是 {gap_h / 24:.1f} 天前，"
                                        f"现在又回来了"})
            if st["days"] >= 3:
                found.append({"kind": "streak", "topic": topic,
                              "detail": f"「{topic}」已经连续/累计 {st['days']} 天出现"})
        # 异常：当前连续活跃时长 / 深夜时段
        h = time.localtime(now).tm_hour
        if 2 <= h <= 6:
            found.append({"kind": "hour", "topic": "",
                          "detail": f"当前是 {h} 点——这个时段的活动在历史里很少见"})
        sess_start = self.store.get_state("session_start_wall")
        last_obs = self.store.conn.execute(
            "SELECT max(ts_wall) m FROM observations").fetchone()["m"]
        if last_obs and (not sess_start or now - last_obs > self.cfg.gap_close_min * 60):
            self.store.set_state("session_start_wall", last_obs)
            sess_start = last_obs
        if sess_start and now - sess_start >= 6 * 3600:
            found.append({"kind": "long_session", "topic": "",
                          "detail": f"这一轮已经连续活跃 {(now - sess_start) / 3600:.1f} 小时"})
        # 去重（同 topic 只留最有价值的一条），按 kind 优先级
        prio = {"repeat": 0, "revival": 1, "streak": 2, "long_session": 3, "hour": 4}
        best: dict[str, dict] = {}
        for f in found:
            k = f["topic"] or f["kind"]
            if k not in best or prio[f["kind"]] < prio[best[k]["kind"]]:
                best[k] = f
        return sorted(best.values(), key=lambda f: prio[f["kind"]])[:limit]
