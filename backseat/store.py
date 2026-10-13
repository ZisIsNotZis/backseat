"""SQLite 存储层：六张表 + 会话流视图 + state kv。WAL，幂等。

设计公理（DESIGN §10）：表结构描述引擎本体——观察 -> 理解 -> 表达/执行。
插件与输入输出手段不是表，是字段值（sensor / channel 列）。

  observations 原始观察（一切传感器的落点）
  messages 记忆金字塔（level: small/medium/big，汉诺塔合并）
  kb           唤起记忆              expressions 表达与行为（speak/propose/act）
  metrics      逐调用计量（tokens/缓存命中/时延）
  state        kv：USER_MODEL / ANCHOR / 计数器 / 预算水位
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

SCHEMA = """
PRAGMA journal_mode=WAL;

-- 原始观察：任何传感器的产物。重内容落文件（ref），轻内容内联（content）
CREATE TABLE IF NOT EXISTS observations (
  id        INTEGER PRIMARY KEY,
  ts_wall   REAL NOT NULL,
  ts_mono   REAL NOT NULL,
  sensor    TEXT NOT NULL,             -- 插件id: screen|atspi|dbus|git|cli|…
  kind      TEXT NOT NULL,             -- sensor内种类: screen.frame|atspi.snapshot|…
  display   TEXT NOT NULL DEFAULT 'primary',
  key       TEXT,                      -- 去重身份（phash/标题/hash）
  content   TEXT,                      -- 轻量内联载荷（json/text）
  ref       TEXT,                      -- 重内容文件路径（jpeg等）
  ref_full  TEXT,                      -- 高保真副本（pin/大事件帧）
  hash      TEXT,                      -- 内容哈希（phash/sha）
  bytes     INTEGER NOT NULL DEFAULT 0,
  pinned    INTEGER NOT NULL DEFAULT 0,-- 1=预算驱逐豁免
  meta      TEXT                       -- json（display、尺寸、触发信息…）
);
CREATE INDEX IF NOT EXISTS idx_obs_ts ON observations(ts_wall);
CREATE INDEX IF NOT EXISTS idx_obs_sensor ON observations(sensor, kind);

-- 记忆金字塔：每条都是 message；合并 = n条低级折叠成1条高级（汉诺塔）
CREATE TABLE IF NOT EXISTS messages (
  id        INTEGER PRIMARY KEY,
  level     TEXT NOT NULL,             -- small|medium|big
  ts_start  REAL NOT NULL, ts_end REAL NOT NULL,
  content   TEXT,                      -- small: diff | medium: 摘要+意图弧线 | big: epoch叙事
  intent    TEXT,                      -- small 专用
  type      TEXT,                      -- small: diff|transcript|describe|anchor|sensor
  source    TEXT,                      -- small: vlm|atspi|sensor
  model     TEXT,
  payload   TEXT,                      -- json：级别专属扩展（callback_tags/patterns/…）
  keys      TEXT,                      -- json 唤起词表（medium/big 必备）
  obs_ids   TEXT,                      -- json，small: 溯源观察
  user_model_delta TEXT,              -- json，合并时产出
  tokens_in INTEGER DEFAULT 0, tokens_out INTEGER DEFAULT 0,
  folded    INTEGER NOT NULL DEFAULT 0,-- 1=已被上级吸收（行不删，可下钻）
  parent_id INTEGER REFERENCES messages(id)
);
CREATE INDEX IF NOT EXISTS idx_msg_level ON messages(level, folded);
CREATE INDEX IF NOT EXISTS idx_msg_ts ON messages(ts_start);

-- 唤起记忆（keys=定位符=唤起词）
CREATE TABLE IF NOT EXISTS kb (
  id        INTEGER PRIMARY KEY,
  keys      TEXT NOT NULL,             -- json
  desc      TEXT NOT NULL,
  kind      TEXT NOT NULL DEFAULT 'fact',  -- fact|reminder|contact|proposal
  special   TEXT,                      -- '!always' | '!at=...'
  open      INTEGER NOT NULL DEFAULT 1,
  last_shown REAL,
  created   REAL NOT NULL, updated REAL NOT NULL
);

-- 表达与行为：引擎向外的一切动作。channel 只是字段，插件不是表
CREATE TABLE IF NOT EXISTS expressions (
  id        INTEGER PRIMARY KEY,
  ts_wall   REAL NOT NULL,
  kind      TEXT NOT NULL,             -- speak|propose|act
  content   TEXT NOT NULL,
  channel   TEXT,                      -- 出口插件id: danmaku|notification|avatar|…
  mood      TEXT,                      -- 语义情绪闭集（SCHEMA.md），出口自行映射
  salience  REAL,                      -- 0~1 重要度（出口映射字号/打断等级）
  dedup_key TEXT,
  source_refs TEXT,                   -- json 溯源：[{table:'messages',id:…},…]
  status    TEXT NOT NULL DEFAULT 'sent',  -- sent|shown|accepted|rejected|done|failed
  meta      TEXT
);
CREATE INDEX IF NOT EXISTS idx_expr_ts ON expressions(ts_wall);

-- 逐调用计量（M2 起 LLM 通道使用；全量 trajectory 走 JSONL，此表供统计/反调预算）
CREATE TABLE IF NOT EXISTS metrics (
  id        INTEGER PRIMARY KEY,
  ts_wall   REAL NOT NULL,
  ts_mono   REAL NOT NULL,
  purpose   TEXT NOT NULL,             -- witness:anchor|witness:diff|compress:mid|compress:big|persona:…
  model     TEXT,
  tokens_in INTEGER NOT NULL DEFAULT 0,
  tokens_out INTEGER NOT NULL DEFAULT 0,
  cached_tokens INTEGER NOT NULL DEFAULT 0,
  latency_ms REAL
);
CREATE INDEX IF NOT EXISTS idx_metrics_ts ON metrics(ts_wall);

CREATE TABLE IF NOT EXISTS state (
  key       TEXT PRIMARY KEY,
  value     TEXT NOT NULL              -- json
);

CREATE VIEW IF NOT EXISTS session_stream AS
  SELECT * FROM messages WHERE level='small' AND folded=0 ORDER BY ts_start;"""


class Store:
    def __init__(self, path: Path | str) -> None:
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # —— 通用行插入（dict/list 自动 json 化）——
    def insert(self, table: str, **cols) -> int:
        cols = {k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v)
                for k, v in cols.items()}
        names = ",".join(cols)
        qs = ",".join("?" * len(cols))
        cur = self.conn.execute(f"INSERT INTO {table}({names}) VALUES({qs})", tuple(cols.values()))
        self.conn.commit()
        return cur.lastrowid

    # —— state kv ——
    def set_state(self, key: str, value) -> None:
        self.conn.execute(
            "INSERT INTO state(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value, ensure_ascii=False)))
        self.conn.commit()

    def get_state(self, key: str, default=None):
        row = self.conn.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    # —— 视图与水位 ——
    def session_stream(self, limit: int = 200) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM session_stream ORDER BY ts_start DESC LIMIT ?", (limit,)).fetchall()

    def storage_used(self) -> int:
        row = self.conn.execute("SELECT COALESCE(SUM(bytes),0) AS b FROM observations").fetchone()
        return int(row["b"])

    # —— L0 原始层驱逐（DESIGN §8）——
    def _anchor_obs_ids(self) -> set[int]:
        """最近一条 anchor 消息引用的观察 id（ANCHOR 引用帧 = 驱逐豁免）。"""
        row = self.conn.execute(
            "SELECT obs_ids FROM messages WHERE level='small' AND type='anchor' "
            "ORDER BY id DESC LIMIT 1").fetchone()
        if not row or not row["obs_ids"]:
            return set()
        try:
            return {int(x) for x in json.loads(row["obs_ids"])}
        except (ValueError, TypeError):
            return set()

    def evict_l0(self, budget_bytes: int, chunk: int = 200) -> dict:
        """L0 原始层驱逐：按 ts_wall FIFO（最旧优先）直到水位 <= budget_bytes 或
        无可驱逐对象。删 ref 指向的 JPEG 并把该行 ref 置 NULL（保留行——时间线与
        flashback 降级为纯文字，DESIGN §8）。

        豁免（不删 ref，保留 ref_full）：pinned=1；最近 anchor 的 obs_ids 引用帧；
        ref_full IS NOT NULL 的全分辨率副本。文件已缺失不报错（计入 missing_files）。

        置 bytes=0 前把原帧大小写入该行 meta JSON 的 evicted_bytes（无损溯源：
        storage_used() 只认 bytes，原始大小与是否被驱逐仍可查）。
        返回 {freed_bytes, evicted, exempt, missing_files, used_before, used_after}。

        路径级处理：帧名是秒级 `%H%M%S`，同一 tick 的大事件与采样用同一 now 抓帧 →
        可有多行 observations 指向同一文件（screen.py）。故按 ref 路径而非按行处理：
        · 删除前先查同一路径的所有行，只要任一行豁免（pinned / anchor 引用 /
          ref_full）就整条路径跳过（否则会删掉豁免行仍引用的文件、并使另一行留悬空
          ref —— DESIGN §8 豁免失效）；
        · unlink 成功后把所有 ref=该路径 的行一并 ref=NULL、bytes=0、写 evicted_bytes
          （行与 storage_used() 语义一致，无残留幻影字节）；
        · 同一路径的 freed_bytes / evicted 只计一次（按当前行的 bytes 记该路径大小）。

        注：多行共享同一路径时 storage_used() 本就重复累计该文件；驱逐把共享行全归零，
        故 used_after（重算）可能比 used_before - freed_bytes 降得更多——freed_bytes
        是每路径一次的真实释放量，used_after 才是权威水位。
        """
        used = self.storage_used()
        stats = {"freed_bytes": 0, "evicted": 0, "exempt": 0, "missing_files": 0,
                 "used_before": used, "used_after": used}
        if used <= budget_bytes:
            return stats
        protected = self._anchor_obs_ids()
        after_wall, after_id = -1e18, 0
        done_refs: set[str] = set()  # 本轮已判定/已删的路径，防同页重复处理
        while used > budget_bytes:
            rows = self.conn.execute(
                "SELECT id, ts_wall, ref, ref_full, bytes, pinned, meta FROM observations "
                "WHERE ref IS NOT NULL AND (ts_wall > ? OR (ts_wall = ? AND id > ?)) "
                "ORDER BY ts_wall, id LIMIT ?",
                (after_wall, after_wall, after_id, chunk)).fetchall()
            if not rows:
                break  # 无可驱逐对象（ref 全 NULL 或只剩豁免）
            for r in rows:
                after_wall, after_id = r["ts_wall"], r["id"]
                if used <= budget_bytes:
                    break
                ref = r["ref"]
                if ref in done_refs:
                    continue
                same = self.conn.execute(
                    "SELECT id, bytes, pinned, ref_full, meta FROM observations "
                    "WHERE ref = ?", (ref,)).fetchall()
                done_refs.add(ref)
                if any(s["pinned"] or s["id"] in protected or s["ref_full"]
                       for s in same):
                    # 同一路径有豁免行 → 整条路径不删（否则豁免失效 + 普通行悬空）
                    stats["exempt"] += 1
                    continue
                try:
                    Path(ref).unlink()
                except OSError:
                    # 文件已缺失/不可删：计入统计，仍置 NULL 计账，不阻断驱逐
                    stats["missing_files"] += 1
                freed = int(r["bytes"] or 0)  # 同一路径只按此计一次
                for s in same:
                    meta = self._meta_of(s["meta"])
                    meta["evicted_bytes"] = int(s["bytes"] or 0)
                    self.conn.execute(
                        "UPDATE observations SET ref=NULL, bytes=0, meta=? WHERE id=?",
                        (json.dumps(meta, ensure_ascii=False), s["id"]))
                    used -= int(s["bytes"] or 0)  # 归零所有共享行，保持水位一致
                stats["freed_bytes"] += freed
                stats["evicted"] += 1
            self.conn.commit()
        stats["used_after"] = self.storage_used()
        return stats

    @staticmethod
    def _meta_of(raw) -> dict:
        try:
            meta = json.loads(raw) if raw else {}
        except (ValueError, TypeError):
            meta = {}
        return meta if isinstance(meta, dict) else {}

    def close(self) -> None:
        self.conn.close()
