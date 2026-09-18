"""Backseat 守护进程入口：python -m backseat [--config TOML] [--data-dir DIR] [--once N]

M1 形态：屏幕传感器（L0）-> 事件总线 -> observations 落库。理解引擎（L1+）属 M2。
- 帧入选 = observations 行（ref=JPEG 路径，bytes/hash/key 全记录）
- 大事件 = 同一帧通道，meta.trigger 记种类；事件同时上总线（未来引擎订阅）
- Ctrl-C 优雅退出；--once N 跑 N 个 tick 即退出（冒烟/验收用）
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

from .bus import EventBus
from .clock import Stamp, fmt_wall
from .config import load_config
from .sensors.screen import Frame, ScreenSensor
from .store import Store


def build(cfg, store: Store, bus: EventBus) -> ScreenSensor:
    frames_dir = Path(cfg.data_dir).expanduser() / "frames"

    def on_frame(f: Frame, meta: dict) -> int:
        size = f.jpeg_path.stat().st_size if f.jpeg_path and f.jpeg_path.exists() else 0
        return store.insert(
            "observations",
            ts_wall=f.stamp.wall, ts_mono=f.stamp.mono,
            sensor="screen", kind="screen.notable", display="primary",
            key=f"phash:{f.phash:016x}",
            ref=str(f.jpeg_path) if f.jpeg_path else None,
            hash=f"phash:{f.phash:016x}", bytes=size,
            meta=meta)

    bus.subscribe(lambda e: None)  # M2 引擎订阅位；当前事件消费仅落库，预留挂点
    return ScreenSensor(
        display=cfg.display, frames_dir=frames_dir,
        sample_low=cfg.sample_low, sample_mid=cfg.sample_mid,
        sample_high=cfg.sample_high, ring_seconds=cfg.ring_seconds,
        batch_max=cfg.batch_max, process_window=cfg.process_window,
        blank_std=cfg.blank_std, gap_close_min=cfg.gap_close_min,
        emit=bus.publish_sync, on_frame=on_frame)


async def run(cfg, ticks: int | None) -> None:
    data = Path(cfg.data_dir).expanduser()
    data.mkdir(parents=True, exist_ok=True)
    store = Store(data / "backseat.db")
    bus = EventBus()
    sensor = build(cfg, store, bus)

    print(f"backseat M0+M1: display={sensor.display or '$DISPLAY'} "
          f"data={data} tier={sensor.tier}（Ctrl-C 退出）", flush=True)
    n = 0
    try:
        while ticks is None or n < ticks:
            t0 = time.monotonic()
            sensor.tick(Stamp.now())
            n += 1
            if n % 60 == 0:
                s = sensor.stats
                print(f"[{fmt_wall(time.time())}] tick={n} tier={sensor.tier} "
                      f"采样={s['samples']} 入选={s['admitted']} 事件={s['events']} "
                      f"空帧={s['blank']} 抓帧失败={s['grab_fail']} "
                      f"存储={store.storage_used()>>20}MB", flush=True)
            # 目标 1s tick；抓帧耗时从休眠中扣除
            await asyncio.sleep(max(0.2, 1.0 - (time.monotonic() - t0)))
    except KeyboardInterrupt:
        pass
    finally:
        s = sensor.stats
        print(json.dumps({"final": s, "storage_bytes": store.storage_used()}),
              flush=True)
        store.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="backseat")
    ap.add_argument("--config", type=Path, default=None)
    ap.add_argument("--data-dir", type=Path, default=None)
    ap.add_argument("--once", type=int, default=None, metavar="N",
                    help="跑 N 个 tick 后退出（默认一直跑）")
    args = ap.parse_args(argv)
    cfg = load_config(args.config)
    if args.data_dir:
        cfg.data_dir = str(args.data_dir)
    try:
        asyncio.run(run(cfg, args.once))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
