"""双轨时间戳：墙钟（用户时间）+ 单调钟（区间计算）。所有表两列都有。"""

from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Stamp:
    wall: float  # unix epoch 秒
    mono: float  # time.monotonic() 秒

    @classmethod
    def now(cls) -> "Stamp":
        return cls(wall=time.time(), mono=time.monotonic())


def fmt_wall(wall: float) -> str:
    return time.strftime("%m-%d %H:%M:%S", time.localtime(wall))
