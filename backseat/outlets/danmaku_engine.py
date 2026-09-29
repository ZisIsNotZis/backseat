#!/usr/bin/env python3
"""Danmaku engine v2（移植自 ~/vibe/danmaku，AGPL 同源）— lightweight, high-performance, click-through.

Why v1 stuttered and v2 doesn't:
- v1 repainted ONE fullscreen 2560x1440 ARGB window every 16ms (~15MB clear +
  ~100 blits, software raster) -> blew the frame budget.
- v2 rasterizes each comment ONCE into a small pixmap inside its own tiny
  override-redirect ARGB window, then only calls move() (~XMoveWindow).
  Moving an X11 window repositions the existing buffer; nothing is
  re-rasterized, no fullscreen damage. Per frame: N tiny async requests.

Layout (top-first, exact tight packing):
- lanes top -> bottom; a comment enters the FIRST lane (or 2-lane band for
  large size) whose most recent comment satisfies
      delta >= T * max(w_new, w_last) / (W + max(w_new, w_last))
  i.e. the gap shrinks to zero at exactly one end (right-edge entry, or
  catch-up exactly at leader exit). No margin, no wasted slack.
- feed/engine split: push() at real time; the engine never sees the future.

CLI:
  danmaku_engine.py script timeline.json [--duration S]   # real-time playback
  danmaku_engine.py daemon [--duration S]                 # wait for push()
"""
import atexit
import ctypes
import ctypes.util
import json
import sys

from PyQt5.QtCore import QElapsedTimer, QObject, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QBrush, QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen, QPixmap
from PyQt5.QtWidgets import QApplication, QWidget

FONT_FAMILY = "Noto Sans CJK SC"
SMALL_PX = 30            # 用户校准：原 15 太小，2 倍
LIFETIME_MS = 9000       # fixed lifetime for every comment (longer = faster)
LANE_PAD = 8
LANE_TOP = 70
LANE_BOTTOM_GAP = 40
OUTLINE = 2              # small text outline
OUTLINE_L = 3            # large text outline
EPS_MS = 2               # numeric slack only, no visual waste

# --- raw X11 input-shape helper (cached display) -----------------------------
_dpy = None


def _xlibs():
    x11 = ctypes.CDLL(ctypes.util.find_library("X11"))
    xext = ctypes.CDLL(ctypes.util.find_library("Xext"))
    x11.XOpenDisplay.restype = ctypes.c_void_p
    return x11, xext


def _get_dpy():
    global _dpy
    if _dpy is None:
        x11, _ = _xlibs()
        _dpy = x11.XOpenDisplay(None)
        atexit.register(lambda: None)  # display dies with process
    return _dpy


def force_empty_input_shape(xid):
    """Empty input region => pointer events pass through the window."""
    dpy = _get_dpy()
    if not dpy:
        return
    _, xext = _xlibs()
    xext.XShapeCombineRectangles(
        ctypes.c_void_p(dpy), ctypes.c_uint(xid), ctypes.c_int(2),  # ShapeInput
        0, 0, None, 0, ctypes.c_int(0), ctypes.c_int(0))            # ShapeSet


# --- one tiny window per comment --------------------------------------------
class CommentWindow(QWidget):
    def __init__(self, pm):
        super().__init__(None,
                         Qt.Tool | Qt.FramelessWindowHint
                         | Qt.WindowStaysOnTopHint
                         | Qt.X11BypassWindowManagerHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setWindowFlag(Qt.WindowTransparentForInput, True)
        self.pm = pm
        self.setFixedSize(pm.size())

    def paintEvent(self, event):
        p = QPainter(self)
        p.drawPixmap(0, 0, self.pm)   # painted once; moves cause no repaint


# --- engine ------------------------------------------------------------------
class Engine(QObject):
    pushRequested = pyqtSignal(str, str, str)   # text, color, size

    def __init__(self, duration_ms=None):
        super().__init__()
        screen = QApplication.primaryScreen().geometry()
        self.W, self.H = screen.width(), screen.height()

        self.font_s = QFont(FONT_FAMILY)
        self.font_s.setPixelSize(SMALL_PX)
        self.font_s.setBold(True)
        self.fm_s = QFontMetrics(self.font_s)
        self.lane_h = self.fm_s.height() + LANE_PAD
        n_lanes = (self.H - LANE_TOP - LANE_BOTTOM_GAP) // self.lane_h
        self.lanes = [{"y": LANE_TOP + i * self.lane_h, "last_start": None, "last_w": 0}
                      for i in range(n_lanes)]

        # large font must fit a 2-lane band
        large_px = min(2 * self.lane_h - 10, 80)  # 用户校准：原 40，2 倍
        self.font_l = QFont(FONT_FAMILY)
        self.font_l.setPixelSize(large_px)
        self.font_l.setBold(True)
        self.fm_l = QFontMetrics(self.font_l)

        self.active = []
        self.feed_closed = False
        self.duration_ms = duration_ms
        self.stats = {"placed": 0, "skipped": 0, "large": 0, "fallback_small": 0}
        self.pushRequested.connect(self._do_push)

        self.clock = QElapsedTimer()
        self.anim = QTimer(self)
        self.anim.setInterval(16)
        self.anim.timeout.connect(self.tick)

    # public API (thread-safe: emit -> queued slot on main thread)
    def push(self, text, color="#FFFFFF", size="small"):
        self.pushRequested.emit(str(text), str(color), str(size))

    def close_feed(self):
        self.feed_closed = True

    def start(self):
        self.clock.start()
        self.anim.start()

    # --- allocation -----------------------------------------------------------
    def _can_enter(self, lane, now, w):
        if lane["last_start"] is None:
            return True
        m = max(w, lane["last_w"])
        return (now - lane["last_start"]) >= LIFETIME_MS * m / (self.W + m) + EPS_MS

    def _do_push(self, text, color, size):
        now = self.clock.elapsed()
        if self.duration_ms is not None and now > self.duration_ms:
            return
        want_large = (size == "large")
        placed = False
        if want_large:
            w = self.fm_l.horizontalAdvance(text)
            for i in range(len(self.lanes) - 1):
                if (self._can_enter(self.lanes[i], now, w)
                        and self._can_enter(self.lanes[i + 1], now, w)):
                    placed = self._spawn(text, color, [i, i + 1], large=True)
                    break
        if not placed:
            if want_large:
                self.stats["fallback_small"] += 1   # no 2-lane band yet -> small
            w = self.fm_s.horizontalAdvance(text)
            for i in range(len(self.lanes)):        # topmost lane first
                if self._can_enter(self.lanes[i], now, w):
                    placed = self._spawn(text, color, [i], large=False)
                    break
        if placed:
            self.stats["placed"] += 1
        else:
            self.stats["skipped"] += 1

    def _spawn(self, text, color, lane_ids, large):
        font = self.font_l if large else self.font_s
        fm = self.fm_l if large else self.fm_s
        pad = (OUTLINE_L if large else OUTLINE) + 2
        w = fm.horizontalAdvance(text) + 2 * pad
        h = fm.height() + 2 * pad
        pm = QPixmap(w, h)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addText(pad, pad + fm.ascent(), font, text)
        ol = OUTLINE_L if large else OUTLINE
        p.strokePath(path, QPen(QColor(0, 0, 0), ol * 2,
                                Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.fillPath(path, QBrush(QColor(color)))
        p.end()

        win = CommentWindow(pm)
        win.show()
        force_empty_input_shape(int(win.winId()))
        now = self.clock.elapsed()
        for li in lane_ids:
            self.lanes[li]["last_start"] = now
            self.lanes[li]["last_w"] = w
        self.active.append({
            "win": win, "w": w, "y": self.lanes[lane_ids[0]]["y"],
            "start": now, "v": (self.W + w) / LIFETIME_MS,
        })
        if large:
            self.stats["large"] += 1
        return True

    # --- animation: move only, never repaint -----------------------------------
    def tick(self):
        now = self.clock.elapsed()
        keep = []
        for d in self.active:
            age = now - d["start"]
            if age >= LIFETIME_MS:
                d["win"].close()
                d["win"].deleteLater()
                continue
            d["win"].move(int(self.W - d["v"] * age), d["y"])
            keep.append(d)
        self.active = keep
        if self.feed_closed and not self.active and now > 500:
            self.anim.stop()
            print("engine stats:", self.stats, flush=True)
            QApplication.quit()


# --- CLI: real-time timeline player ------------------------------------------
def run_timeline(path, duration_s):
    app = QApplication(sys.argv)
    app.setApplicationName("danmaku")
    with open(path) as f:
        entries = sorted(json.load(f), key=lambda e: e["t"])
    dur_ms = int(duration_s * 1000) if duration_s else None
    eng = Engine(duration_ms=dur_ms)
    eng.start()

    state = {"i": 0}
    def feeder():
        now = eng.clock.elapsed()
        while state["i"] < len(entries) and entries[state["i"]]["t"] * 1000 <= now:
            e = entries[state["i"]]
            eng.push(e.get("text", ""), e.get("color", "#FFFFFF"), e.get("size", "small"))
            state["i"] += 1
        if state["i"] >= len(entries) and now > entries[-1]["t"] * 1000 + 300:
            eng.close_feed()
    ft = QTimer(eng, interval=20, timeout=feeder)
    ft.start()
    sys.exit(app.exec_())


def run_daemon():
    app = QApplication(sys.argv)
    app.setApplicationName("danmaku")
    eng = Engine()
    eng.start()
    sys.exit(app.exec_())




# --- Backseat Outlet 适配：stdin JSON-lines 守护（一行一条：{"text","color","size"}）---
def run_stdin_daemon():
    """从 stdin 读 JSON 行实时上屏。Backseat 守护进程以子进程方式托管本引擎。"""
    import threading
    app = QApplication(sys.argv)
    app.setApplicationName("backseat-danmaku")
    eng = Engine()
    eng.start()

    def feed():
        import os
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
                eng.push(e.get("text", ""), e.get("color", "#FFFFFF"),
                         e.get("size", "small"))
            except (json.JSONDecodeError, TypeError):
                pass
    threading.Thread(target=feed, daemon=True).start()
    sys.exit(app.exec_())


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "script":
        dur = float(sys.argv[4]) if len(sys.argv) > 4 and sys.argv[3] == "--duration" else None
        run_timeline(sys.argv[2], dur)
    elif len(sys.argv) > 1 and sys.argv[1] == "daemon":
        run_daemon()
    else:
        run_stdin_daemon()
