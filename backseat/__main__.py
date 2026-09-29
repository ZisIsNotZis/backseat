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
from .compressor import Compressor
from .config import load_config
from .model import ModelClient, ModelError
from .persona import Persona
from .sensors.screen import Frame, ScreenSensor
from .store import Store
from .trajectory import Trajectory
from .witness import Witness


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
    sensor = ScreenSensor(
        display=cfg.display, frames_dir=frames_dir,
        sample_low=cfg.sample_low, sample_mid=cfg.sample_mid,
        sample_high=cfg.sample_high, ring_seconds=cfg.ring_seconds,
        batch_max=cfg.batch_max, process_window=cfg.process_window,
        blank_std=cfg.blank_std, gap_close_min=cfg.gap_close_min,
        emit=bus.publish_sync, on_frame=on_frame)
    return sensor, None


def build_witness(cfg, store: Store) -> Witness | None:
    """L1 见证层 + L2 压缩层：模型凭据齐全才启用（LITELLM_BASE_URL + api_key_env）。"""
    model = ModelClient.from_config(cfg)
    if model is None:
        return None
    comp = Compressor(store, model, Trajectory(cfg.data_dir), cfg)
    wit = Witness(store, model, Trajectory(cfg.data_dir), cfg)
    wit.compressor = comp  # 引擎循环节后调用 comp.maybe_merge('small'|'medium')
    wit.persona = Persona("danmaku-jun", store, model, Trajectory(cfg.data_dir), cfg)
    return wit


async def run(cfg, ticks: int | None) -> None:
    data = Path(cfg.data_dir).expanduser()
    data.mkdir(parents=True, exist_ok=True)
    store = Store(data / "backseat.db")
    bus = EventBus()
    sensor, _ = build(cfg, store, bus)
    witness = build_witness(cfg, store)
    if witness is None:
        print("L1 见证层关闭（缺 LITELLM_BASE_URL 或 API key）——仅 L0 采集", flush=True)
    else:
        print(f"L1 见证层启用：{cfg.model}", flush=True)

    print(f"backseat M0-M5: display={sensor.display or '$DISPLAY'} "
          f"data={data} tier={sensor.tier}（Ctrl-C 退出）", flush=True)
    n = 0
    now_wall = time.time()
    try:
        while ticks is None or n < ticks:
            t0 = time.monotonic()
            sensor.tick(Stamp.now())
            try:
                if witness is not None:
                    if witness.process_pending():
                        witness.compressor.maybe_merge("small")  # K条 small → mid
                        witness.compressor.maybe_merge("medium")  # M条 mid → big
                    pc = store.get_state("persona_cursor", 0)
                    for r in store.conn.execute(
                            "SELECT * FROM messages WHERE level='small' AND id>? "
                            "ORDER BY id LIMIT 5", (pc,)).fetchall():
                        witness.persona.on_fact(r)
                        store.set_state("persona_cursor", r["id"])
                    # 周期 digest：无高重要度事件也定期开口（频率校准旋钮）
                    if now_wall - store.get_state("last_digest", 0.0) >= cfg.digest_min * 60:
                        witness.persona.on_digest()
                        store.set_state("last_digest", now_wall)
            except ModelError as e:
                print(f"[engine] LLM 暂不可用，下轮重试：{e}", flush=True)
            n += 1
            if n % 60 == 0:
                s = sensor.stats
                print(f"[{fmt_wall(time.time())}] tick={n} tier={sensor.tier} "
                      f"采样={s['samples']} 入选={s['admitted']} 事件={s['events']} "
                      f"空帧={s['blank']} 抓帧失败={s['grab_fail']} "
                      f"存储={store.storage_used()>>20}MB", flush=True)
            # 目标 1s tick；抓帧耗时从休眠中扣除
            await asyncio.sleep(max(0.2, 1.0 - (time.monotonic() - t0)))
            now_wall = time.time()
    except KeyboardInterrupt:
        pass
    finally:
        s = sensor.stats
        w = witness.stats if witness else {}
        p = witness.persona.stats if witness else {}
        if witness:
            witness.persona.outlet.close()
        print(json.dumps({"final": {"sensor": s, "witness": w, "persona": p},
                          "storage_bytes": store.storage_used()}),
              flush=True)
        store.close()


def show_stats(data_dir: Path) -> None:
    store = Store(Path(data_dir).expanduser() / "backseat.db")
    m = store.conn.execute(
        "SELECT count(*) c, sum(tokens_in) tin, sum(tokens_out) tout, "
        "sum(cached_tokens) tc FROM metrics").fetchone()
    calls, tin, tout, tc = m["c"] or 0, m["tin"] or 0, m["tout"] or 0, m["tc"] or 0
    obs = store.conn.execute(
        "SELECT count(*) c, coalesce(sum(bytes),0) b FROM observations").fetchone()
    by_level = store.conn.execute(
        "SELECT level, count(*) c FROM messages GROUP BY level").fetchall()
    print(f"调用={calls}  tokens: in={tin} out={tout} "
          f"缓存命中率={tc / tin * 100:.0f}%" if tin else "尚无调用")
    print(f"观察={obs['c']} 帧（{obs['b'] >> 20}MB）  "
          f"消息={ {r['level']: r['c'] for r in by_level} }")
    store.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="backseat")
    default_cfg = Path(__file__).resolve().parent.parent / "config.toml"
    ap.add_argument("--config", type=Path,
                    default=default_cfg if default_cfg.exists() else None)
    ap.add_argument("--data-dir", type=Path, default=None)
    ap.add_argument("--once", type=int, default=None, metavar="N",
                    help="跑 N 个 tick 后退出（默认一直跑）")
    ap.add_argument("--stats", action="store_true", help="打印计量汇总后退出")
    args = ap.parse_args(argv)
    cfg = load_config(args.config)
    if args.data_dir:
        cfg.data_dir = str(args.data_dir)
    if args.stats:
        show_stats(Path(cfg.data_dir))
        return 0
    try:
        asyncio.run(run(cfg, args.once))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
