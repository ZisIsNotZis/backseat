"""唤起记忆（M4）：KB 定位符命中自动注入 + 特殊键 + last_shown 防重 + flashback。

- keys 双重身份：写入时是检索词，浮现时是唤起词（kb 行 keys 命中近期事实 → 注入）
- `!always`：周期重放（inject_every_s，默认 30min）；`!at=YYYY-MM-DD HH:MM`：到点提醒
- last_shown 防重：同 key 命中 60min 内不重复注入；compaction 时清零防漏（由压缩层调用）
- flashback：显式查询（keys/时间范围）→ 逐字回放 + 帧引用；obs 已驱逐 → 文字降级
- 帧引用按 parent_id 递归下钻到叶子 small 汇集 obs_ids（有界深度 + 防环，big 才能回指）；
  下钻后仍无 obs_ids（真孤儿）或下钻被截断 → degraded=1 且给出 degraded_reason，不静默返回空集
"""

from __future__ import annotations

import json
import time

KB_HIT_DEDUPE_S = 3600.0  # 同 key 60min 不重复注入（WALKTHROUGH T4）
ALWAYS_DEFAULT_S = 1800.0
FLASHBACK_MAX_DEPTH = 4  # 证据链下钻上限：big→medium→small 只需 2 层，留余量并截断异常深链/环


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
        """命中 mid/big（默认）→ 递归下钻到叶子 small。
        返回 [{id, level, content, keys, refs:[obs ref...], degraded, degraded_reason}]；
        degraded=True 当且仅当无法给出可用帧：下钻后仍无 obs_ids，或帧已被驱逐。
        degraded_reason ∈ None | 'no_leaf_obs' | 'chain_truncated' | 'frames_evicted'。"""
        q = ("SELECT * FROM messages WHERE level IN ('medium','big') AND ("
             + " OR ".join("keys LIKE ?" for _ in (keys or ["%"])) + ")"
             + " AND ts_end >= ? AND ts_start <= ? ORDER BY ts_end DESC LIMIT ?")
        params = [f'%"{k}"%' for k in (keys or ["%"])] + \
                 [t_start or 0, t_end or time.time() + 1, limit]
        rows = self.store.conn.execute(q, params).fetchall()
        out = []
        for r in rows:
            obs_ids, truncated = self._gather_obs_ids(r["id"], 0, {r["id"]})
            refs, degraded, reason = [], False, None
            for oid in sorted(obs_ids):
                obs = self.store.conn.execute(
                    "SELECT ref, ref_full FROM observations WHERE id=?", (oid,)).fetchone()
                if obs and obs["ref"]:
                    refs.append(obs["ref"])
                else:
                    degraded, reason = True, "frames_evicted"  # DESIGN §4 链路 F
            if not obs_ids:
                # 下钻到底仍无证据（孤儿 big / 下钻截断）：显式降级，不静默空集
                degraded = True
                reason = "chain_truncated" if truncated else "no_leaf_obs"
            elif truncated and not degraded:
                # 仍给出部分帧，但链不完整；帧已被驱逐时保留更可操作的 frames_evicted
                reason = "chain_truncated"
            out.append({"id": r["id"], "level": r["level"], "content": r["content"],
                        "keys": json.loads(r["keys"] or "[]"),
                        "refs": refs, "degraded": degraded, "degraded_reason": reason})
        return out

    def _gather_obs_ids(self, msg_id: int, depth: int,
                        seen: set[int]) -> tuple[set[int], bool]:
        """按 parent_id 递归下钻汇集证据：子行自带 obs_ids 就用，为空则继续下钻
        （medium 的 obs_ids 恒为 NULL，big 的引用只能从叶子 small 汇总）。
        返回 (obs_ids, truncated)：truncated=True 表示有子行因深度上限或成环未被下钻。"""
        children = self.store.conn.execute(
            "SELECT id, obs_ids FROM messages WHERE parent_id=? ORDER BY id",
            (msg_id,)).fetchall()
        obs_ids: set[int] = set()
        truncated = False
        for c in children:
            ids = self._parse_obs_ids(c["obs_ids"])
            if ids:
                obs_ids |= ids
                continue
            if depth + 1 > FLASHBACK_MAX_DEPTH:
                truncated = True  # 深度上限：不再下钻
                continue
            if c["id"] in seen:
                truncated = True  # 成环：parent_id 回指已访问行
                continue
            sub, cut = self._gather_obs_ids(c["id"], depth + 1, seen | {c["id"]})
            obs_ids |= sub
            truncated = truncated or cut
        return obs_ids, truncated

    @staticmethod
    def _parse_obs_ids(raw) -> set[int]:
        if not raw:
            return set()
        try:
            return {int(x) for x in json.loads(raw)}
        except (ValueError, TypeError):
            return set()
