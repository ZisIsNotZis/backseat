#!/usr/bin/env python3
"""M0″ 抽样表：把决策 JSONL 变成人看的 Markdown（题面正确率的输入）。

  python3 scripts/m0pp_sample.py --decisions ~/backseat-data/grounding --out sheet.md
  python3 scripts/m0pp_sample.py ... --full   # 带材料正文的详细版

默认表为可读版：标题截断到约 120 字符，材料只列 locator（被引用的；没引用就列全部），
不堆正文。每个时刻一行：moment id、标题、题面、材料 locator、评论正文，以及一列空的
「正确/错误」等人填。不替人判，不伪造标签；决策少于 50 条时全部列出并在表头说明。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

DEFAULT_LIMIT = 50


def load_decisions(path: Path) -> list[dict]:
    files = sorted(path.glob("*.jsonl")) if path.is_dir() else [path]
    out: list[dict] = []
    for f in files:
        if not f.is_file():
            continue
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(d, dict):
                out.append(d)
    return out


def _cell(text) -> str:
    s = "" if text is None else str(text)
    return s.replace("|", "\\|").replace("\n", " ")


def _clip(text, limit: int) -> str:
    s = "" if text is None else str(text)
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _material_cells(d: dict, full: bool) -> list[str]:
    mats = list(d.get("materials") or [])
    cited = [m for m in mats if m.get("id") in (d.get("citations") or [])]
    chosen = cited or mats
    out = []
    for m in chosen:
        loc = f"`{_cell(m.get('locator'))}`"
        if full:
            loc += " " + _cell((m.get("excerpt") or "")[:180])
        out.append(loc)
    return out


def render(decisions: list[dict], limit: int = DEFAULT_LIMIT,
           full: bool = False) -> str:
    total = len(decisions)
    shown = decisions[:limit]
    lines = [
        "# M0″ 题面抽样表",
        "",
        f"- 决策总数：{total}；本表列出：{len(shown)}"
        + ("（不足 50，全部列出）" if total < DEFAULT_LIMIT else ""),
        "- 触发的来源：批量时刻是**真实 pi 会话用户消息**（source=session）代替实时窗口标题；"
        "本表是 dry-run 抽样，不是伪造的时刻。",
        "- 材料只列 locator（被引用的；没引用就列全部）" if not full
        else "- 材料列 locator + 正文摘要（--full 详细版）。",
        "- 请只判「题面说对了在哪个项目/哪一步」，不看流畅度。",
        "- 在最后一列填 `正确` 或 `错误`。",
        "",
        "| moment id | 标题/触发 | 题面 | 材料（locator" + (" + 摘要" if full else "") + "） | 评论 | 正确/错误 |",
        "|---|---|---|---|---|---|",
    ]
    for d in shown:
        mats = _material_cells(d, full)
        title = _clip(d.get("title"), 120) if not full else d.get("title")
        lines.append("| " + " | ".join([
            _cell(d.get("moment_id") or d.get("ts")),
            _cell(title),
            _cell(d.get("thesis")),
            "<br>".join(mats) if mats else "（无材料）",
            _cell(d.get("content") or f"（沉默：{d.get('reason')}）"),
            "",
        ]) + " |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="m0pp_sample")
    ap.add_argument("--decisions", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    ap.add_argument("--full", action="store_true",
                    help="详细版：标题不截断，材料带正文摘要")
    args = ap.parse_args(argv)

    decisions = load_decisions(args.decisions)
    if not decisions:
        print("没有决策可抽样（exit 2）")
        return 2
    args.out.write_text(render(decisions, args.limit, full=args.full),
                        encoding="utf-8")
    print(f"抽样表已写：{args.out}（决策 {len(decisions)} 条，"
          f"表内 {min(len(decisions), args.limit)} 条）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
