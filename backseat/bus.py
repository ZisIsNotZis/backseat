"""事件总线：传感器 -> 引擎 的唯一通道。

- 全事件带双轨时间戳
- (kind, key, 时间窗) 去重，防多传感器双发
- 大事件种类是封闭枚举：新增须改这里（DESIGN §10）
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .clock import Stamp


class EventKind:
    """事件种类。FRAME 是常规帧；其余为封闭枚举的大事件。

    大事件清单（DESIGN §10.G1）：窗口切换/workspace切换/全屏切换/恢复空闲/display拓扑变化。
    锁屏不是事件（Round 2：静止画面由 pHash 门控自处理）。
    """

    FRAME = "frame"  # pHash 门控放行的常规帧（key=phash）
    WINDOW_SWITCH = "window_switch"  # key=window title
    WORKSPACE_SWITCH = "workspace_switch"
    FULLSCREEN_TOGGLE = "fullscreen_toggle"
    IDLE_RESUME = "idle_resume"  # >gap_close_min 后首次活动
    DISPLAY_CHANGE = "display_change"

    BIG_EVENTS = frozenset({
        WINDOW_SWITCH, WORKSPACE_SWITCH,
        FULLSCREEN_TOGGLE, IDLE_RESUME, DISPLAY_CHANGE,
    })


@dataclass(frozen=True, slots=True)
class Event:
    kind: str
    key: str  # 同 kind 内的去重身份（phash / 窗口标题 / …）
    stamp: Stamp
    payload: dict[str, Any] = field(default_factory=dict)


class EventBus:
    def __init__(self, dedup_window: float = 1.0) -> None:
        self._dedup_window = dedup_window
        self._last: dict[tuple[str, str], float] = {}
        self._subs: list[Callable[[Event], Any]] = []

    def subscribe(self, handler: Callable[[Event], Any]) -> None:
        self._subs.append(handler)

    def _admit(self, event: Event) -> bool:
        """去重门。返回 False 表示时间窗内重复。"""
        k = (event.kind, event.key)
        last = self._last.get(k)
        if last is not None and event.stamp.mono - last < self._dedup_window:
            return False
        self._last[k] = event.stamp.mono
        return True

    def publish_sync(self, event: Event) -> bool:
        """同步发布（传感器采集循环用）。返回 False 表示被去重丢弃。"""
        if not self._admit(event):
            return False
        for h in self._subs:
            r = h(event)
            if asyncio.iscoroutine(r):
                raise TypeError("同步发布不支持异步订阅者")
        return True

    async def publish(self, event: Event) -> bool:
        """异步发布（引擎侧用）。返回 False 表示被去重丢弃。"""
        if not self._admit(event):
            return False
        for h in self._subs:
            r = h(event)
            if asyncio.iscoroutine(r):
                await r
        return True
