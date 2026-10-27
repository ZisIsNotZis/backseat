#!/usr/bin/env python3
"""M0″ 判官：读 <data_dir>/grounding/decisions-*.jsonl，机械判三门。

  python3 scripts/m0pp_eval.py --decisions ~/backseat-data/grounding
  python3 scripts/m0pp_eval.py --decisions FILE --labels labels.json

有据率 = 每条 speak 的引用都能解析到本次材料（id 存在、项目一致、locator 文件仍在且
sha256 可复算时一致）的条数 / speak 总数。
无材料出口数 = materials==[] 却仍产出表达的条数。
错题数 = 引用材料的 project != 本条决策 project 的引用条数。
题面正确率只在给 --labels 时算：labels 是 {moment_id: "correct"|"wrong"}；没给就打印 N/A，
不猜。抽样表由 scripts/m0pp_sample.py 生成，人填 正确/错误。

退出码：0 三门全绿；1 任一不绿；2 没有决策（不能确立）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


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


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _fetched_ts(material: dict) -> float | None:
    try:
        return datetime.fromisoformat(
            str(material.get("fetched_at")).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def verify_material(material: dict, project: str | None) -> tuple[bool | None, str]:
    """locator 还能不能复算：True/False；None = 这类 locator 不可复算（git 动态输出）。

    文件类 locator 检查文件仍在；带 #Lx-y 的按行切片重算 sha256；会话 locator 只查文件。
    文件在取材料之后被改过时返回 "stale"（仍是可解析的取时快照），不算伪造。
    """
    loc = str(material.get("locator") or "")
    sha = material.get("sha256")
    excerpt_field = material.get("excerpt")
    if sha and isinstance(excerpt_field, str) and _sha256(excerpt_field) != sha:
        return False, "material excerpt does not match sha256"
    if loc.startswith("git:"):
        return None, "dynamic git output"
    if "@" in loc:
        f = Path(loc.rsplit("@", 1)[0])
        return (f.is_file(), f"session file {'exists' if f.is_file() else 'missing'}")
    if "#L" in loc:
        path_part, rng = loc.rsplit("#L", 1)
        try:
            start_s, end_s = rng.split("-", 1)
            start, end = int(start_s), int(end_s)
        except ValueError:
            return False, "bad line range"
        f = Path(path_part)
        if not f.is_absolute():
            f = Path(project or "") / f
        if not f.is_file():
            return False, f"file missing: {f}"
        lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        excerpt = "\n".join(lines[start - 1:end])
        if sha and _sha256(excerpt) != sha:
            fetched = _fetched_ts(material)
            if fetched is not None and f.stat().st_mtime > fetched + 1:
                # 取材料之后文件被改过：摘要是取时的快照，判 stale 而不是伪造。
                return "stale", "file changed after fetched_at"
            return False, "sha256 mismatch"
        return True, "file+sha ok"
    f = Path(loc)
    if f.is_file():
        return True, "file exists"
    return False, f"locator missing: {loc}"


def evaluate(decisions: list[dict], labels: dict | None) -> dict:
    speaks = [d for d in decisions if d.get("outcome") == "speak"]
    silences = [d for d in decisions if d.get("outcome") != "speak"]
    no_material = 0
    wrong = 0
    cited = 0
    stale = 0
    bad_details: list[str] = []
    for d in speaks:
        mats = {m.get("id"): m for m in d.get("materials") or []}
        if not mats:
            no_material += 1
        ok = bool(d.get("citations"))
        if not d.get("citations"):
            bad_details.append(f"{d.get('moment_id')}: uncited")
        for cid in d.get("citations") or []:
            m = mats.get(cid)
            if m is None:
                ok = False
                bad_details.append(f"{d.get('moment_id')}: citation {cid} not in materials")
                continue
            if m.get("project") != d.get("project"):
                wrong += 1
                ok = False
                bad_details.append(f"{d.get('moment_id')}: cross-project {cid}")
                continue
            ver, detail = verify_material(m, d.get("project"))
            if ver == "stale":
                stale += 1
            elif ver is False:
                ok = False
                bad_details.append(f"{d.get('moment_id')}: {cid} {detail}")
        if ok:
            cited += 1
    speak_total = len(speaks)
    rate = (cited / speak_total * 100.0) if speak_total else 0.0
    silence_reasons = Counter(d.get("reason") for d in silences)

    thesis_rate = None
    thesis_n = 0
    if labels is not None:
        judged = [d for d in decisions if d.get("moment_id") in labels]
        thesis_n = len(judged)
        if thesis_n:
            good = sum(1 for d in judged
                       if labels.get(d.get("moment_id")) == "correct")
            thesis_rate = good / thesis_n * 100.0

    ok = (speak_total > 0 and cited == speak_total and no_material == 0
          and wrong == 0)
    return {"decisions": len(decisions), "speak_total": speak_total,
            "cited": cited, "citation_rate": rate, "no_material_exits": no_material,
            "wrong_project_citations": wrong, "stale_citations": stale,
            "silence_reasons": dict(silence_reasons),
            "thesis_correct_rate": thesis_rate, "thesis_judged": thesis_n,
            "bad": bad_details, "pass": ok}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="m0pp_eval")
    ap.add_argument("--decisions", type=Path, required=True,
                    help="决策 JSONL 文件或目录")
    ap.add_argument("--labels", type=Path, default=None,
                    help="可选：{moment_id: correct|wrong} JSON")
    ap.add_argument("--json", type=Path, default=None, help="把报告另存为 JSON")
    args = ap.parse_args(argv)

    decisions = load_decisions(args.decisions)
    if not decisions:
        print("没有决策可判（exit 2，不能确立）")
        return 2
    labels = None
    if args.labels is not None:
        try:
            labels = json.loads(args.labels.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"labels 读不了: {e}")
            return 2
        if not isinstance(labels, dict):
            print("labels 必须是 {moment_id: correct|wrong}")
            return 2

    report = evaluate(decisions, labels)
    print(f"决策数={report['decisions']}  speak={report['speak_total']}  "
          f"silence={report['decisions'] - report['speak_total']}")
    print(f"有据率={report['citation_rate']:.1f}% "
          f"({report['cited']}/{report['speak_total']})")
    print(f"无材料出口数={report['no_material_exits']}")
    print(f"错题数（跨项目引用）={report['wrong_project_citations']}")
    if report["stale_citations"]:
        print(f"取材料后文件被改过（stale，仍算可解析）={report['stale_citations']}")
    print("沉默原因=" + (json.dumps(report["silence_reasons"], ensure_ascii=False)
                        if report["silence_reasons"] else "{}"))
    if report["thesis_correct_rate"] is None:
        print("题面正确率=N/A（未给 --labels；由 m0pp_sample.py 交人判）")
    else:
        print(f"题面正确率={report['thesis_correct_rate']:.1f}% "
              f"(人标 {report['thesis_judged']} 条)")
    for b in report["bad"][:10]:
        print("  ✗ " + b)
    if args.json:
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                             encoding="utf-8")
    print("判定=" + ("PASS" if report["pass"] else "FAIL"))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
