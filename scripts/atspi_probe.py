#!/usr/bin/env python3
"""AT-SPI 探针：某个应用在这条 a11y 总线上到底暴露了什么（只读，不打印正文）。

为什么要有它：`docs/3_REQUIREMENT.md` 里关于"终端能不能给文本"的结论必须是可复算的读数，
不是印象。AT-SPI2 的节点是 bus 上的对象，`gdbus introspect` 就能看到它实现了哪些接口；
本脚本只数接口、只量长度，**不打印任何节点正文**（屏幕里的字是人的，不是文档的）。

用法：
  python3 scripts/atspi_probe.py                      # 列出所有注册应用（名字 + 节点数）
  python3 scripts/atspi_probe.py ghostty              # 看 ghostty：接口直方图 + 有没有 Text
  python3 scripts/atspi_probe.py ghostty --text-len   # 再量 Text.CharacterCount（只给数字）

前置：会话总线上有 `org.a11y.Bus`（GNOME 默认有）。本脚本用返回的地址连 a11y 总线，
没有 python 绑定也能跑（只依赖 gdbus）。
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections import Counter

MAX_DEPTH = 16


def gdbus(*args: str) -> str:
    return subprocess.run(["gdbus", *args], capture_output=True, text=True).stdout


def a11y_address() -> str:
    out = gdbus("call", "--session", "--dest", "org.a11y.Bus",
                "--object-path", "/org/a11y/bus", "--method", "org.a11y.Bus.GetAddress")
    m = re.search(r"'([^']+)'", out)
    if not m:
        sys.exit("没有 org.a11y.Bus：这条会话没有跑 at-spi（GNOME 下通常有）")
    return m.group(1)


class Bus:
    def __init__(self, addr: str, dest: str):
        self.addr, self.dest = addr, dest

    def call(self, path: str, method: str, *args: str) -> str:
        return gdbus("call", "--address", self.addr, "--dest", self.dest,
                     "--object-path", path, "--method", method, *args)

    def children(self, path: str) -> list[tuple[str, str]]:
        out = self.call(path, "org.a11y.atspi.Accessible.GetChildren")
        return re.findall(r"\('(:[\d.]+)',\s*(?:objectpath\s*)?'([^']+)'\)", out)

    def name(self, path: str) -> str:
        out = self.call(path, "org.freedesktop.DBus.Properties.Get",
                        "org.a11y.atspi.Accessible", "Name")
        m = re.search(r"<'([^']*)'>", out)
        return m.group(1) if m else ""


def walk(bus: Bus, path: str, depth: int, stats: dict, text_len: bool) -> None:
    intro = gdbus("introspect", "--address", bus.addr, "--dest", bus.dest,
                  "--object-path", path)
    ifaces = [i for i in re.findall(r"^  interface ([\w.]+)", intro, re.M)
              if i.startswith("org.a11y.atspi")]
    stats["nodes"] += 1
    for i in ifaces:
        stats["ifaces"][i] += 1
    if "org.a11y.atspi.Text" in ifaces:
        stats["text_nodes"].append((bus.name(path), path))
        if text_len:
            out = bus.call(path, "org.freedesktop.DBus.Properties.Get",
                           "org.a11y.atspi.Text", "CharacterCount")
            stats["char_counts"].append((bus.name(path), re.sub(r"\D", "", out) or "?"))
    if depth >= MAX_DEPTH:
        return
    for child_bus, child_path in bus.children(path):
        walk(Bus(bus.addr, child_bus or bus.dest), child_path, depth + 1, stats, text_len)


def apps(addr: str) -> list[tuple[str, str]]:
    reg = Bus(addr, "org.a11y.atspi.Registry")
    return reg.children("/org/a11y/atspi/accessible/root")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("app", nargs="?", help="应用名（大小写不敏感的相等比较，见不带参数的列表）")
    ap.add_argument("--text-len", action="store_true", help="对每个 Text 节点量 CharacterCount（只给数字）")
    args = ap.parse_args()
    addr = a11y_address()
    roots = apps(addr)
    if not args.app:
        for bus_name, path in roots:
            print(f"{bus_name:>10}  {Bus(addr, bus_name).name(path)}")
        return 0
    hits = [(b, p) for b, p in roots
            if Bus(addr, b).name(p).lower() == args.app.lower()]
    if not hits:
        sys.exit(f"a11y 总线上没有叫 {args.app!r} 的应用；不带参数跑一次看列表")
    for bus_name, path in hits:
        stats = {"nodes": 0, "ifaces": Counter(), "text_nodes": [], "char_counts": []}
        walk(Bus(addr, bus_name), path, 0, stats, args.text_len)
        print(f"{args.app} ({bus_name}): {stats['nodes']} nodes")
        print(f"  ifaces: {dict(stats['ifaces'])}")
        print(f"  nodes with org.a11y.atspi.Text: {len(stats['text_nodes'])}")
        for nm, cc in stats["char_counts"]:
            print(f"    CharacterCount={cc}  name={nm[:40]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
