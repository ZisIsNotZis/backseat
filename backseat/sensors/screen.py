"""屏幕传感器（L0）：采样节奏控制器 + RAM 环 + 挑选器 + 大事件检测。无 LLM。

- 采样分档（DESIGN §10 G2）：低 60s / 中 20s / 高 2s，由 hamming 动力学 EMA 决定；
  升档即时、降档需连续 3 次低于阈值（防抖）
- 大事件（封闭枚举，DESIGN §10.G1）直通：立即抓帧入选，事件上总线
- 帧先进 RAM 环（~120s 不落盘），挑选器每处理窗从环中取 hamming≥6 的帧
  （对最近入选值链式比较，按时序 ≤batch_max）交 on_frame 落库——采集频率≠处理频率
- 空帧拦截：32x32 灰度近均值（std<blank_std）视为空白，不入环、直接降低档

传感器是纯逻辑：探针（x11 适配器）与落库回调由外部注入，可离线测试、可换平台。
"""

from __future__ import annotations

import os
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..bus import Event, EventKind
from ..clock import Stamp
from . import x11
from .phash import hamming, phash64, phash_hex

# EMA 分档阈值（64 位 hamming 尺度）
EMA_UP_HIGH = 8.0   # 单次尖峰或 EMA 达此值 → 高档（即时）
EMA_UP_MID = 2.0    # EMA 达此值 → 中档
EMA_DOWN = 1.0      # EMA 低于此值连续 LOW_STREAK_N 次 → 低档（防抖）
LOW_STREAK_N = 3


@dataclass(slots=True)
class Frame:
    stamp: Stamp
    phash: int
    blank: bool
    jpeg_path: Path | None = None   # 落盘路径（由 on_frame 写入后回填亦可）


@dataclass
class ScreenSensor:
    display: str = ""
    frames_dir: Path | None = None
    jpeg_width: int = 1600          # DESIGN §8：常规 1600px JPEG q6
    emit: Callable[[Event], None] = field(default=lambda e: None)
    on_frame: Callable[[Frame, dict], int | None] = field(default=lambda f, m: None)
    blank_std: float = 6.0
    sample_low: float = 60.0
    sample_mid: float = 20.0
    sample_high: float = 2.0
    ring_seconds: float = 120.0
    batch_max: int = 6
    process_window: float = 20.0
    gap_close_min: float = 30.0

    def __post_init__(self) -> None:
        self.display = self.display or os.environ.get("DISPLAY", ":0")
        self.tier = "mid"
        self._ema: float | None = None
        self._down_streak = 0
        self._last_sample_mono = 0.0
        self._last_process_mono = 0.0
        self._last_sampled: Frame | None = None
        self._last_admitted_phash: int | None = None
        self._ring: deque[Frame] = deque()
        self._win: dict | None = None
        self._last_wall: float | None = None
        self._was_idle = False
        self._geometry: tuple[int, int] | None = None
        self.stats = {"samples": 0, "blank": 0, "grab_fail": 0,
                      "admitted": 0, "events": 0}

    # —— 对外主入口：守护进程按 ~1s tick 驱动 ——
    def tick(self, now: Stamp | None = None) -> None:
        now = now or Stamp.now()
        # 墙钟跳变 = 睡眠/合盖（DESIGN §10.G1：系统睡眠是天然会话边界）
        if self._last_wall is not None and now.wall - self._last_wall > self.gap_close_min * 60:
            self._was_idle = True
            self._big_event(now, EventKind.IDLE_RESUME, "wake",
                            {"gap_s": now.wall - self._last_wall})
        self._last_wall = now.wall

        self._probe_big_events(now)

        if now.mono - self._last_sample_mono >= self.tier_seconds:
            self._last_sample_mono = now.mono
            self._sample(now)
        if now.mono - self._last_process_mono >= self.process_window:
            self._last_process_mono = now.mono
            self._process(now)

    @property
    def tier_seconds(self) -> float:
        return {"low": self.sample_low, "mid": self.sample_mid,
                "high": self.sample_high}[self.tier]

    # —— 大事件探针（封闭枚举；新增须改 EventKind.BIG_EVENTS 与此处）——
    def _probe_big_events(self, now: Stamp) -> None:
        win = x11.window(self.display)
        if win is None:
            return
        prev = self._win
        self._win = win
        if prev is not None:
            if win["workspace"] != prev["workspace"]:
                self._big_event(now, EventKind.WORKSPACE_SWITCH, f"ws{win['workspace']}",
                                {"from": prev["workspace"], "to": win["workspace"]})
            if win["wid"] != prev["wid"] and win["title"] != prev["title"]:
                self._big_event(now, EventKind.WINDOW_SWITCH,
                                win["title"] or str(win["wid"]),
                                {"to": win["title"], "wm_class": win["wm_class"]})
            if win["fullscreen"] != prev["fullscreen"]:
                self._big_event(now, EventKind.FULLSCREEN_TOGGLE, str(win["fullscreen"]),
                                {"to": win["fullscreen"]})
            if self._was_idle and (idle := x11.idle_s(self.display)) is not None and idle < 5.0:
                self._was_idle = False
                self._big_event(now, EventKind.IDLE_RESUME, "resume", {"idle_s": idle})
        if (idle := x11.idle_s(self.display)) is not None and idle > self.gap_close_min * 60:
            self._was_idle = True

    def _big_event(self, now: Stamp, kind: str, key: str, payload: dict) -> None:
        """大事件直通：事件上总线 + 抓帧立即入选（不经环）。"""
        self.stats["events"] += 1
        self.emit(Event(kind, key, now, dict(payload)))
        f, meta = self._grab(now)
        if f is not None and not f.blank:
            meta["trigger"] = kind
            meta["event_payload"] = payload
            self._admit(f, meta)

    # —— 采样 ——
    def _sample(self, now: Stamp) -> None:
        self.stats["samples"] += 1
        geo = x11.screen_geometry(self.display)
        if geo is not None:
            if self._geometry is not None and geo != self._geometry:
                self._big_event(now, EventKind.DISPLAY_CHANGE, f"{geo[0]}x{geo[1]}",
                                {"from": self._geometry, "to": geo})
            self._geometry = geo
        f, _ = self._grab(now)
        if f is None:
            self.stats["grab_fail"] += 1
            return
        if f.blank:
            self.stats["blank"] += 1
            self._set_tier("low")
            return
        if self._last_sampled is not None and not self._last_sampled.blank:
            h = hamming(f.phash, self._last_sampled.phash)
            self._ema = h if self._ema is None else 0.7 * self._ema + 0.3 * h
            self._retier(h)
        self._last_sampled = f
        self._push_ring(f)

    def _retier(self, h: float) -> None:
        e = self._ema or 0.0
        if h >= EMA_UP_HIGH or e >= EMA_UP_HIGH:
            self._set_tier("high")
        elif e >= EMA_UP_MID:
            self._set_tier("mid")
        elif e < EMA_DOWN:
            self._down_streak += 1
            if self._down_streak >= LOW_STREAK_N:
                self._set_tier("low")
        else:
            self._down_streak = 0

    def _set_tier(self, tier: str) -> None:
        if tier != self.tier:
            self.tier = tier
            self._down_streak = 0

    # —— 抓帧（x11 适配器；测试时以 probe 覆写替代）——
    probe_grab: Callable[[Stamp], tuple[Frame | None, dict]] | None = None

    @staticmethod
    def _scaled(geo: tuple[int, int], max_w: int) -> tuple[int, int]:
        """全屏尺寸 → ≤max_w 的偶数缩放尺寸（编码器要求偶数）。"""
        w, h = geo
        if w > max_w:
            h = round(h * max_w / w)
            w = max_w
        return w - w % 2, h - h % 2

    def _grab(self, now: Stamp) -> tuple[Frame | None, dict]:
        if self.probe_grab is not None:
            return self.probe_grab(now)
        if self.frames_dir is None:
            return None, {}
        d = self.frames_dir / time.strftime("%Y%m%d", time.localtime(now.wall))
        d.mkdir(parents=True, exist_ok=True)
        out = d / (time.strftime("%H%M%S", time.localtime(now.wall)) + ".jpg")
        geo = self._geometry or (1920, 1080)
        gray = x11.grab(self.display, f"{geo[0]}x{geo[1]}",
                        self._scaled(geo, self.jpeg_width), out)
        if gray is None:
            return None, {}
        f = Frame(now, phash64(gray), self._is_blank(gray), out)
        return f, {}

    def _is_blank(self, gray: bytes) -> bool:
        n = len(gray)
        mean = sum(gray) / n
        var = sum((b - mean) ** 2 for b in gray) / n
        return var ** 0.5 < self.blank_std

    # —— 环与挑选 ——
    def _push_ring(self, f: Frame) -> None:
        self._ring.append(f)
        cutoff = f.stamp.mono - self.ring_seconds
        while self._ring and self._ring[0].stamp.mono < cutoff:
            self._ring.popleft()

    def _process(self, now: Stamp) -> None:
        """挑选器：环中对最近入选 hamming≥6 的帧，按时序 ≤batch_max 入选。

        冷启动（尚无入选基准）：首个非空帧直接入选，建立基准。
        """
        base = self._last_admitted_phash
        picks: list[Frame] = []
        for f in self._ring:
            if f.blank:
                continue
            if base is None or hamming(f.phash, base) >= 6:
                picks.append(f)
                base = f.phash
        for f in picks[-self.batch_max:]:
            self._admit(f, {})
        self._ring.clear()  # 未入选帧自然淘汰（宁多勿漏由高档采样保证）

    def _admit(self, f: Frame, meta: dict) -> None:
        self._last_admitted_phash = f.phash
        self.stats["admitted"] += 1
        obs_id = self.on_frame(f, meta)
        payload = {"key": phash_hex(f.phash)}
        if obs_id is not None:
            payload["obs_id"] = obs_id
        self.emit(Event(EventKind.FRAME, phash_hex(f.phash), f.stamp, payload))
