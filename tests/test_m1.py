"""M1 冒烟：pHash、空帧拦截、采样分档、RAM 环挑选器、大事件检测、总线联动。

纯逻辑离线测试（探针与抓帧注入），真实抓帧仅在 DISPLAY 可用时附加验证。
运行：python3 tests/test_m1.py
"""

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from backseat.bus import EventKind
from backseat.clock import Stamp
from backseat.sensors import screen as scr
from backseat.sensors.phash import hamming, phash64, phash_hex
from backseat.sensors.screen import Frame, ScreenSensor


def gray_bytes(pattern: str) -> bytes:
    """确定性的 32x32 灰度：'flat'=全 128，'half'=左黑右白，'noise'=伪随机。"""
    if pattern == "flat":
        return bytes([128]) * 1024
    if pattern == "half":
        return bytes([10 if (i % 32) < 16 else 240 for i in range(1024)])
    if pattern == "soft":  # 半幅渐变（非空白，但与 flat 哈希应差异大）
        return bytes((i % 32) * 8 for i in range(1024))
    x = 12345
    out = []
    for _ in range(1024):
        x = (x * 1103515245 + 12345) & 0x7FFFFFFF
        out.append(x & 0xFF)
    return bytes(out)


def main() -> None:
    # --- pHash 基本性质 ---
    h_flat = phash64(gray_bytes("flat"))
    h_half = phash64(gray_bytes("half"))
    h_soft = phash64(gray_bytes("soft"))
    assert phash64(gray_bytes("flat")) == h_flat, "确定性失败"
    assert hamming(h_flat, h_half) >= 6, "差异图像 hamming 应过阈值"
    assert hamming(h_half, h_soft) >= 6
    print(f"phash OK: flat~half={hamming(h_flat, h_half)} "
          f"half~soft={hamming(h_half, h_soft)} flat~soft={hamming(h_flat, h_soft)}")

    # --- 空帧拦截 ---
    with tempfile.TemporaryDirectory() as td:
        s = ScreenSensor(frames_dir=Path(td))
        assert s._is_blank(gray_bytes("flat")), "全平灰度应为空帧"
        assert not s._is_blank(gray_bytes("half")), "黑白分明的画面不是空帧"

    # --- 离线传感器：注入合成帧，验证分档/环/挑选 ---
    events: list = []
    admitted: list[Frame] = []
    with tempfile.TemporaryDirectory() as td:
        s = ScreenSensor(frames_dir=Path(td), emit=events.append,
                         on_frame=lambda f, m: (admitted.append(f), len(admitted))[1],
                         sample_low=60.0, sample_mid=20.0, sample_high=2.0,
                         ring_seconds=120.0, batch_max=3, process_window=20.0)
        # 屏蔽探针：无窗口信息、无空闲、无真实抓帧
        scr.x11.window = lambda d: None
        scr.x11.idle_s = lambda d: None
        scr.x11.screen_geometry = lambda d: (2560, 1440)

        t0 = time.monotonic()
        state = {"q": []}

        def fake_grab(now):
            q = state["q"]
            if not q:
                return None, {}
            p = q.pop(0)
            if p is None:
                return None, {}
            return Frame(now, p, blank=False), {}

        s.probe_grab = fake_grab

        # 静止画面：连续低 hamming → 降档 low（防抖 3 次）
        base = h_half
        state["q"] = [base, base, base, base]
        for _ in range(6):  # 中档 20s 节奏内多 tick；直接调 _sample 驱动
            t0 += 20.0
            s._last_sample_mono = -1e9  # 强制到期
            s._sample(Stamp(time.time(), t0))
        assert s.tier == "low", f"静止画面应降为 low，实际 {s.tier}"
        assert s._ema is not None and s._ema < 1.0
        print(f"tier OK: 静止→low（ema={s._ema:.2f}）")

        # 尖峰：hamming≥8 → 即时升 high
        spike = base ^ 0xFFFF  # 16 位翻转
        state["q"] = [spike]
        s._last_sample_mono = -1e9
        t0 += 40.0
        s._sample(Stamp(time.time(), t0))
        assert s.tier == "high", f"尖峰应即时升 high，实际 {s.tier}"
        print("tier OK: 尖峰→high（即时）")

        # 挑选器：环内 hamming≥6 链式比较，batch_max=3 截断
        s._last_admitted_phash = base
        s._ring.clear()
        prev = base
        for k in range(5):  # 每帧对前一帧 hamming≥10 → 链式全过阈，但只取最后 3
            prev = prev ^ (0x3FF << (2 * k + 1))
            s._ring.append(Frame(Stamp(time.time(), t0 + 50.0 + k), prev, False))
        s._process(Stamp(time.time(), t0 + 70.0))
        assert len(admitted) == 3, f"batch_max=3 应只入选 3 帧，实际 {len(admitted)}"
        assert s._last_admitted_phash == prev, "链式比较基准应更新到最后入选帧"
        print("picker OK: hamming≥6 链式比较 + batch 截断")

    # --- 大事件检测（离线注入窗口状态）---
    events.clear()
    with tempfile.TemporaryDirectory() as td:
        s = ScreenSensor(frames_dir=Path(td), emit=events.append,
                         on_frame=lambda f, m: None)
        wins = [
            {"wid": 0x1, "title": "term", "wm_class": "St", "fullscreen": False, "workspace": 0},
            {"wid": 0x2, "title": "browser", "wm_class": "FF", "fullscreen": False, "workspace": 0},
            {"wid": 0x2, "title": "browser", "wm_class": "FF", "fullscreen": True, "workspace": 1},
        ]
        it = iter(wins[1:])  # 预置当前窗口为 wins[0]，后续 tick 依次返回 wins[1], wins[2]
        scr.x11.window = lambda d: next(it, wins[-1])
        scr.x11.idle_s = lambda d: None
        scr.x11.screen_geometry = lambda d: None
        s.probe_grab = lambda now: (None, {})  # 事件照发，无帧
        s._win = wins[0]  # 预置：第一次 tick 从 wins[1] 开始比对
        s.tick(Stamp(time.time(), t0 + 100.0))   # wid/title 变 → window_switch
        s.tick(Stamp(time.time(), t0 + 101.0))   # fullscreen+workspace 变
        kinds = [e.kind for e in events]
        assert kinds.count(EventKind.WINDOW_SWITCH) == 1, kinds
        assert kinds.count(EventKind.FULLSCREEN_TOGGLE) == 1, kinds
        assert kinds.count(EventKind.WORKSPACE_SWITCH) == 1, kinds
        print("big events OK: window/fullscreen/workspace 各检出一枚")

        # 空闲恢复
        events.clear()
        idle_state = {"v": 1800.0}
        scr.x11.idle_s = lambda d: idle_state["v"]
        s._was_idle = True
        idle_state["v"] = 2.0
        s.tick(Stamp(time.time(), t0 + 102.0))
        assert EventKind.IDLE_RESUME in [e.kind for e in events]
        print("idle_resume OK")

        # 墙钟跳变（睡眠唤醒）
        events.clear()
        s._was_idle = False
        s.tick(Stamp(time.time() + 3600, t0 + 103.0))
        assert EventKind.IDLE_RESUME in [e.kind for e in events], "墙钟跳变应触发唤醒事件"
        print("wake OK: 睡眠唤醒=天然会话边界")

    # --- 真实抓帧（仅在 X11 会话内验证一次；恢复被测试 patch 掉的探针）---
    import importlib
    import os
    from backseat.sensors import x11
    if os.environ.get("DISPLAY"):
        importlib.reload(x11)  # 上面各块把 x11 探针换成了假身，这里还原
    if os.environ.get("DISPLAY"):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "probe.jpg"
            geo = x11.screen_geometry(os.environ["DISPLAY"])
            assert geo, "xdpyinfo 取尺寸失败"
            gray = x11.grab(os.environ["DISPLAY"], f"{geo[0]}x{geo[1]}",
                            (geo[0] - geo[0] % 2, geo[1] - geo[1] % 2), out)
            if gray is not None:
                assert len(gray) == 1024 and out.stat().st_size > 1000
                print(f"real grab OK: {geo[0]}x{geo[1]} → {out.stat().st_size}B jpeg, "
                      f"phash={phash_hex(phash64(gray))}")
            else:
                print("real grab SKIP: ffmpeg 抓帧失败（可能无活跃显示）")
    print("\nM1 smoke: ALL PASS")


if __name__ == "__main__":
    main()
