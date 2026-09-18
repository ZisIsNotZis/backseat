"""X11 低层探针（Linux 适配器，核心零改动的前提）：

- grab()：ffmpeg x11grab 一帧两产物——1600px JPEG（重内容）+ 32x32 灰度原始字节（pHash 用）
- window()：活动窗口标题 / WM_CLASS / 全屏态 / 当前 workspace
- idle_s()：XScreenSaver IDLETIME（ctypes，缺 libXss 时优雅降级为 None）
- geometry()：屏幕尺寸（display 拓扑变化检测）

所有调用带超时；工具缺失返回 None，由上层决定降级。
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import tempfile
from pathlib import Path


def _run(cmd: list[str], timeout: float = 5.0) -> str | None:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout if r.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def screen_geometry(display: str) -> tuple[int, int] | None:
    out = _run(["xdpyinfo", "-display", display])
    if out:
        for line in out.splitlines():
            if line.startswith("  dimensions:"):
                wh = line.split()[1]
                w, h = wh.split("x")
                return int(w), int(h)
    return None


def grab(display: str, size: str, scaled: tuple[int, int], out_jpeg: Path) -> bytes | None:
    """抓一帧：JPEG（缩放到 scaled）落 out_jpeg，返回 32x32 灰度原始字节。失败返回 None。"""
    with tempfile.NamedTemporaryFile(suffix=".gray", delete=False) as tf:
        gray_path = Path(tf.name)
    try:
        r = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "x11grab", "-video_size",
             size, "-i", display,
             "-filter_complex",
             f"[0:v]split=2[a][b];[a]scale={scaled[0]}:{scaled[1]}[as];"
             "[b]scale=32:32:flags=area,format=gray[bg]",
             "-map", "[as]", "-q:v", "6", "-update", "1", "-frames:v", "1",
             str(out_jpeg),
             "-map", "[bg]", "-f", "rawvideo", "-frames:v", "1", str(gray_path)],
            capture_output=True, timeout=10.0)
        if r.returncode != 0 or not out_jpeg.exists() or out_jpeg.stat().st_size == 0:
            return None
        gray = gray_path.read_bytes()
        return gray if len(gray) == 1024 else None
    except (OSError, subprocess.TimeoutExpired):
        return None
    finally:
        gray_path.unlink(missing_ok=True)


def window(display: str) -> dict | None:
    """活动窗口信息。返回 {wid, title, wm_class, fullscreen, workspace}。"""
    root = _run(["xprop", "-display", display, "-root", "-notype",
                 "_NET_ACTIVE_WINDOW", "_NET_CURRENT_DESKTOP"])
    if not root:
        return None
    wid = None
    ws = None
    for line in root.splitlines():
        if line.startswith("_NET_ACTIVE_WINDOW"):
            try:
                wid = int(line.split()[-1], 16)
            except ValueError:
                return None
        elif line.startswith("_NET_CURRENT_DESKTOP"):
            try:
                ws = int(line.split("=")[1])
            except (ValueError, IndexError):
                ws = None
    if not wid:
        return None
    info = {"wid": wid, "title": "", "wm_class": "", "fullscreen": False, "workspace": ws}
    wout = _run(["xprop", "-display", display, "-id", hex(wid), "-notype",
                 "_NET_WM_NAME", "WM_CLASS", "_NET_WM_STATE"])
    if wout:
        for line in wout.splitlines():
            if line.startswith("_NET_WM_NAME"):
                info["title"] = line.split("= ", 1)[-1].strip('"') if "= " in line else ""
            elif line.startswith("WM_CLASS"):
                parts = [p.strip().strip('"') for p in line.split("=", 1)[-1].split(",")]
                info["wm_class"] = parts[-1] if parts else ""
            elif line.startswith("_NET_WM_STATE") and "_NET_WM_STATE_FULLSCREEN" in line:
                info["fullscreen"] = True
    return info


class _XssInfo(ctypes.Structure):
    _fields_ = [("window", ctypes.c_ulong), ("state", ctypes.c_int),
                ("kind", ctypes.c_int), ("til_or_since", ctypes.c_ulong),
                ("idle", ctypes.c_ulong), ("eventMask", ctypes.c_ulong)]


def idle_s(display: str) -> float | None:
    """输入空闲秒数（XScreenSaver IDLETIME）。libXss 缺失返回 None。"""
    try:
        xlib = ctypes.cdll.LoadLibrary("libX11.so.6")
        xss = ctypes.cdll.LoadLibrary("libXss.so.1")
        xlib.XOpenDisplay.restype = ctypes.c_void_p
        dpy = xlib.XOpenDisplay(display.encode() if display else None)
        if not dpy:
            return None
        try:
            xss.XScreenSaverAllocInfo.restype = ctypes.POINTER(_XssInfo)
            info = xss.XScreenSaverAllocInfo()
            if not xss.XScreenSaverQueryInfo(ctypes.c_void_p(dpy),
                                             xlib.XDefaultRootWindow(dpy), info):
                return None
            return info.contents.idle / 1000.0
        finally:
            xlib.XCloseDisplay(ctypes.c_void_p(dpy))
    except OSError:
        return None
