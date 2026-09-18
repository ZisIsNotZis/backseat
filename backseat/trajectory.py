"""Trajectory 全量落盘（开发期调试生命线，DESIGN §7.5）+ metrics 落表。

每次 LLM 调用一行 JSONL：{ts, purpose, model, messages 全量, response, usage, latency}。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from .clock import Stamp


class Trajectory:
    def __init__(self, data_dir: Path) -> None:
        self.dir = Path(data_dir).expanduser() / "trajectory"
        self.dir.mkdir(parents=True, exist_ok=True)

    def _file(self, wall: float) -> Path:
        return self.dir / (time.strftime("%Y-%m-%d", time.localtime(wall)) + ".jsonl")

    def record(self, purpose: str, model: str, messages: list,
               response: str, usage: dict) -> None:
        now = Stamp.now()
        line = json.dumps({
            "ts": time.strftime("%H:%M:%S", time.localtime(now.wall)),
            "purpose": purpose, "model": model,
            "messages": messages, "response": response,
            "usage": {k: usage.get(k, 0) for k in ("in", "out", "cached", "latency_ms")},
        }, ensure_ascii=False)
        with open(self._file(now.wall), "a") as f:
            f.write(line + "\n")
