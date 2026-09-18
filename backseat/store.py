"""SQLite 存储层：五张表 + 会话流视图 + state kv。WAL，幂等。

设计公理（DESIGN §10）：表结构描述引擎本体——观察 -> 理解 -> 表达/执行。
插件与输入输出手段不是表，是字段值（sensor / channel 列）。

  observations 原始观察（一切传感器的落点）
  messages 记忆金字塔（level: small/medium/big，汉诺塔合并）
  kb           唤起记忆              expressions 表达与行为（speak/propose/act）
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
  status    TEXT NOT NULL DEFAULT 'sent',  -- sent|shown|accepted|rejected|done
  meta      TEXT
);
CREATE INDEX IF NOT EXISTS idx_expr_ts ON expressions(ts_wall);

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

    def close(self) -> None:
        self.conn.close()
