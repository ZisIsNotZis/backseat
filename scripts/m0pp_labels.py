#!/usr/bin/env python3
"""把人工填过的 M0″ 抽样表转成 m0pp_eval 的 labels JSON。

  python3 scripts/m0pp_labels.py --sheet ~/backseat-data/grounding/sample.md \
      --out ~/backseat-data/grounding/labels.json

只读抽样表的最后一列（`正确` / `错误`）；空着或写别的值的行不猜，跳过并报告。
表的行由 m0pp_sample.py 生成，第一列是 moment id。

退出码：0 至少标了一行；2 没有可读的标签（没标、表缺失或格式不对）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _cells(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip().replace("\\|", "|") for c in s.split("|")]


def parse_sheet(text: str) -> tuple[dict[str, str], int]:
    """返回 ({moment_id: correct|wrong}, 未标/不可判行数)。"""
    labels: dict[str, str] = {}
    skipped = 0
    for line in text.splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = _cells(line)
        if len(cells) < 3 or cells[0] == "moment id" or set(cells[0]) <= {"-"}:
            continue
        verdict = cells[-1].strip()
        moment = cells[0].strip()
        if not moment or not verdict:
            skipped += 1
            continue
        if verdict in ("正确", "对", "✓", "correct"):
            labels[moment] = "correct"
        elif verdict in ("错误", "错", "✗", "wrong"):
            labels[moment] = "wrong"
        else:
            skipped += 1
    return labels, skipped


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="m0pp_labels")
    ap.add_argument("--sheet", type=Path, required=True,
                    help="m0pp_sample.py 生成的 Markdown 抽样表")
    ap.add_argument("--out", type=Path, required=True, help="labels JSON 输出")
    args = ap.parse_args(argv)

    try:
        text = args.sheet.read_text(encoding="utf-8")
    except OSError as e:
        print(f"抽样表读不了: {e}")
        return 2
    labels, skipped = parse_sheet(text)
    if not labels:
        print("没有可读的人工标签（最后一列填 正确/错误）；exit 2")
        return 2
    args.out.write_text(json.dumps(labels, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    wrong = sum(1 for v in labels.values() if v == "wrong")
    print(f"标签已写：{args.out}（标了 {len(labels)} 行：正确 {len(labels) - wrong} / "
          f"错误 {wrong}；跳过未标或不可判 {skipped} 行）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
