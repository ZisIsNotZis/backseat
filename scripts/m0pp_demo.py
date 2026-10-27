#!/usr/bin/env python3
"""M0″ 演示：一次触发走完 题面 → 外部材料 → 带引用的评论 / 沉默。

单条：
  python3 scripts/m0pp_demo.py --title 'zsh · ~/vibe/backseat'
  python3 scripts/m0pp_demo.py --title '…' --no-model          # 确定性：只有材料，不调模型

批量（人的那门：题面正确率抽样）：
  python3 scripts/m0pp_demo.py --moments 50
  从 ~/.pi/agent/sessions/--home-z-vibe-<项目>--/ 里按时间取最近 N 条**真实用户消息**，
  每条的用户文本 + 项目路径当题面，走同一条链，一个时刻一条决策。

退出码：0（说出或沉默都算跑通）；2（参数/环境问题）。
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from backseat.grounding import (  # noqa: E402
    Commentator,
    GroundingEngine,
    MaterialFetcher,
    ProjectResolver,
)
from backseat.model import ModelClient  # noqa: E402
from backseat.store import Store  # noqa: E402
from backseat.trajectory import Trajectory  # noqa: E402


def _user_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") in (None, "text"):
                t = item.get("text")
                if isinstance(t, str):
                    parts.append(t)
        return "\n".join(parts)
    return ""


def session_moments(sessions_root: Path, limit: int,
                    projects_root: Path = Path.home() / "vibe") -> list[dict]:
    """最近的真实用户消息：只取真实 message 行。

    项目映射不靠把目录名里的 `-` 反解成路径（项目名可能自带 `-`），而是对
    projects_root 下每个真实目录算出它的会话目录名再精确匹配；算不出/目录不在
    就跳过。不生成/不补写任何时刻。
    """
    by_dir: dict[str, str] = {}
    if projects_root.is_dir():
        for p in sorted(projects_root.iterdir()):
            if not p.is_dir():
                continue
            slug = str(p.resolve()).strip("/").replace("/", "-")
            by_dir[f"--{slug}--"] = str(p.resolve())
    moments: list[dict] = []
    for sdir in sorted(sessions_root.glob("--*--")):
        if not sdir.is_dir():
            continue
        project = by_dir.get(sdir.name)
        if project is None:
            continue
        for f in sdir.glob("*.jsonl"):
            if not f.is_file():
                continue
            try:
                raw = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for line in raw.splitlines():
                try:
                    d = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if d.get("type") != "message":
                    continue
                msg = d.get("message") or {}
                if msg.get("role") != "user":
                    continue
                text = " ".join(_user_text(msg.get("content")).split())
                if not text:
                    continue
                ts = str(d.get("timestamp") or "")
                moments.append({"ts": ts, "project": project,
                                "text": text, "file": str(f)})
    moments.sort(key=lambda m: m["ts"], reverse=True)
    return moments[:limit]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="m0pp_demo")
    ap.add_argument("--title", default=None,
                    help="触发标题，如 'zsh · ~/vibe/backseat'")
    ap.add_argument("--project", default=None, help="直接指定项目路径（可选）")
    ap.add_argument("--data-dir", type=Path, default=None,
                    help="数据目录（默认临时目录）")
    ap.add_argument("--base-url", default="http://127.0.0.1:4000")
    ap.add_argument("--api-key", default="x")
    ap.add_argument("--model", default="github_copilot/gpt-6-luna")
    ap.add_argument("--no-model", action="store_true",
                    help="不调模型：只取材料，然后沉默（确定性）")
    ap.add_argument("--moments", type=int, default=0,
                    help="批量：从 pi 会话取最近 N 条真实用户消息，每条一个决策")
    ap.add_argument("--sessions-root", type=Path,
                    default=Path.home() / ".pi" / "agent" / "sessions")
    ap.add_argument("--projects-root", type=Path,
                    default=Path.home() / "vibe",
                    help="批量模式下用来把会话目录名精确映射回项目路径")
    args = ap.parse_args(argv)

    if not args.moments and not args.title and not args.project:
        ap.error("需要 --title 或 --project（或 --moments N）")

    data_dir = args.data_dir or Path(tempfile.mkdtemp(prefix="backseat-m0pp-"))
    data_dir.mkdir(parents=True, exist_ok=True)
    store = Store(data_dir / "backseat.db")
    model = None if args.no_model else ModelClient(
        args.base_url, args.api_key, args.model)
    fetcher = MaterialFetcher(projects_root=args.projects_root,
                              sessions_root=args.sessions_root)
    resolver = ProjectResolver(args.projects_root)
    commentator = (None if model is None
                   else Commentator(model, log=None,
                                    trajectory=Trajectory(data_dir)))
    engine = GroundingEngine(store, data_dir, fetcher=fetcher,
                             commentator=commentator, resolver=resolver,
                             log=lambda *_a: None)

    decisions: list[dict] = []
    if args.moments:
        moments = session_moments(args.sessions_root, args.moments,
                                  projects_root=args.projects_root)
        if not moments:
            print(json.dumps({"error": "no_session_moments",
                              "sessions_root": str(args.sessions_root)},
                             ensure_ascii=False))
            store.close()
            return 2
        for m in moments:
            excerpt = m["text"][:80]
            title = f"{m['project']} · {excerpt}"
            moment_id = f"{m['file']}@{m['ts']}"
            d = engine.maybe_ground(title=title, project=m["project"],
                                    moment_id=moment_id, source="session")
            decisions.append(d)
        print(f"批量题面（真实用户消息）: {len(decisions)} 条，"
              f"来自 {len({m['project'] for m in moments})} 个项目")
        for d in decisions:
            print(f"{d['moment_id']} | {d['outcome']}/{d['reason']} | "
                  f"{d['project']} | {str(d['thesis'] or '')[:60]}")
    else:
        title = args.title or args.project
        d = engine.maybe_ground(title=title, project=args.project, source="cli")
        decisions.append(d)
        print(json.dumps(d, ensure_ascii=False, indent=2))

    summary = {"data_dir": str(data_dir), "decisions": len(decisions),
               "speak": engine.stats["speak"], "silence": engine.stats["silence"],
               "model": None if model is None else args.model}
    print(json.dumps(summary, ensure_ascii=False))
    store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
