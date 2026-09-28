"""唤起记忆（M4）：KB 定位符命中自动注入 + 特殊键 + last_shown 防重 + flashback。

- keys 双重身份：写入时是检索词，浮现时是唤起词（kb 行 keys 命中近期事实 → 注入）
- `!always`：周期重放（inject_every_s，默认 30min）；`!at=YYYY-MM-DD HH:MM`：到点提醒
- last_shown 防重：同 key 命中 60min 内不重复注入；compaction 时清零防漏（由压缩层调用）
- flashback：显式查询（keys/时间范围）→ 逐字回放 + 帧引用；obs 已驱逐 → 文字降级
"""

from __future__ import annotations

import json
import time

KB_HIT_DEDUPE_S = 3600.0  # 同 key 60min 不重复注入（WALKTHROUGH T4）
ALWAYS_DEFAULT_S = 1800.0


class KB:
    def __init__(self, store) -> None:
        self.store = store

    # —— 注入（见证层 context 用）——
    def hits_for(self, recent_text: str, now: float | None = None,
                 mark: bool = True) -> list[str]:
        """返回应注入的 [KB命中行] 文本列表。mark=False 时只探测不落账
        （触发判定 peek；真正注入 context 时才记 last_shown）。"""
        now = now or time.time()
        hits: list[str] = []
        for row in self.store.conn.execute(
                "SELECT * FROM kb WHERE open=1 ORDER BY updated DESC").fetchall():
            keys = json.loads(row["keys"])
            special = row["special"] or ""
            due = False
            if special.startswith("!always"):
                every = ALWAYS_DEFAULT_S
                if "=" in special:
                    try:
                        every = float(special.split("=", 1)[1]) * 60
                    except ValueError:
                        pass
                due = (row["last_shown"] or 0) + every <= now
            elif special.startswith("!at="):
                due = self._at_due(special, now)
            else:
                due = any(k and k in recent_text for k in keys)
            if not due:
                continue
            if (row["last_shown"] or 0) + KB_HIT_DEDUPE_S > now:
                continue  # 防重：刚浮现过
            hits.append(f"({row['id']}) {row['desc']}")
            if mark:
                self.store.conn.execute(
                    "UPDATE kb SET last_shown=? WHERE id=?", (now, row["id"]))
        if hits:
            self.store.conn.commit()
        return hits

    @staticmethod
    def _at_due(special: str, now: float) -> bool:
        try:
            import calendar
            at = calendar.timegm(time.strptime(special[4:], "%Y-%m-%d %H:%M")) - time.timezone
            return at <= now and at > now - 86400  # 到点后 24h 内有效
        except ValueError:
            return False

    def clear_last_shown(self) -> None:
        """compaction 边界调用：清零防漏（否则长隐没的 key 永不再浮现）。"""
        self.store.conn.execute("UPDATE kb SET last_shown=NULL")
        self.store.conn.commit()

    # —— flashback（显式查询，M4 门：callback 演示）——
    def flashback(self, keys: list[str] | None = None,
                  t_start: float | None = None, t_end: float | None = None,
                  limit: int = 10) -> list[dict]:
        """命中 mid/big（默认）→ 可下钻 small。返回 [{content, keys, refs:[obs...], degraded}]"""
        q = ("SELECT * FROM messages WHERE level IN ('medium','big') AND ("
             + " OR ".join("keys LIKE ?" for _ in (keys or ["%"])) + ")"
             + " AND ts_end >= ? AND ts_start <= ? ORDER BY ts_end DESC LIMIT ?")
        params = [f'%"{k}"%' for k in (keys or ["%"])] + \
                 [t_start or 0, t_end or time.time() + 1, limit]
        rows = self.store.conn.execute(q, params).fetchall()
        out = []
        for r in rows:
            refs, degraded = [], False
            smalls = self.store.conn.execute(
                "SELECT obs_ids FROM messages WHERE parent_id=? AND obs_ids IS NOT NULL",
                (r["id"],)).fetchall()
            obs_ids: set[int] = set()
            for s in smalls:
                try:
                    obs_ids.update(json.loads(s["obs_ids"]))
                except (ValueError, TypeError):
                    pass
            for oid in sorted(obs_ids):
                obs = self.store.conn.execute(
                    "SELECT ref, ref_full FROM observations WHERE id=?", (oid,)).fetchone()
                if obs and obs["ref"]:
                    refs.append(obs["ref"])
                else:
                    degraded = True  # 帧被驱逐 → 文字降级（DESIGN §4 链路 F）
            out.append({"id": r["id"], "level": r["level"], "content": r["content"],
                        "keys": json.loads(r["keys"] or "[]"),
                        "refs": refs, "degraded": degraded})
        return out
