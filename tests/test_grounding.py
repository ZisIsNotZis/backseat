"""M0″ 外部材料表达链冒烟（离线、独立可跑）。

覆盖：标题→本机项目解析；无项目/无材料→沉默且模型 0 次；伪造引用被拒；跨项目
引用被拒；合法表达落库且 source_refs 带 locator/fetched_at/sha256；决策 JSONL
同时记录 speak 与 silence；m0pp_eval 对干净决策退出 0（有据率 100%、无材料出口 0、
错题 0），篡改一条退出 1；材料只来自被解析项目；抽样表生成；demo 的 --no-model
确定性出口。

运行：python3 tests/test_grounding.py
"""

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from backseat.grounding import (  # noqa: E402
    Commentator, GroundingEngine, Material, MaterialFetcher, ProjectResolver,
    title_from_observation, validate_draft)
from backseat.store import Store  # noqa: E402

CHECKS = 0
ROOT = Path(__file__).parent.parent


def check(cond, msg) -> None:
    global CHECKS
    CHECKS += 1
    assert cond, msg


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class FakeModel:
    model = "fake-grounded"

    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def chat(self, messages):
        self.calls.append(messages)
        out = self.outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        return out, {"in": 10, "out": 10, "cached": 0, "latency_ms": 1}


def draft(citations, thesis="他在 alpha 项目里读 README", content="他正在把 alpha 的说明读顺。",
          thesis_project=None):
    t = thesis if thesis_project is None else {"project": thesis_project, "text": thesis}
    return json.dumps({"thesis": t, "content": content, "citations": citations,
                       "mood": "focused", "salience": 0.4}, ensure_ascii=False)


def make_project(root: Path, name: str, readme: str, docs: dict | None = None) -> Path:
    p = root / name
    (p / "docs").mkdir(parents=True)
    (p / "README.md").write_text(readme, encoding="utf-8")
    for fname, text in (docs or {}).items():
        (p / "docs" / fname).write_text(text, encoding="utf-8")
    subprocess.run(["git", "-C", str(p), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(p), "add", "."], check=True)
    subprocess.run(["git", "-C", str(p), "-c", "user.name=t", "-c", "user.email=t@t",
                    "commit", "-q", "-m", "init"], check=True)
    return p


def make_sessions(sessions_root: Path, project: Path) -> Path:
    fetcher = MaterialFetcher(sessions_root=sessions_root)
    sdir = fetcher.session_dir_for(project)
    sdir.mkdir(parents=True)
    lines = [
        {"type": "session", "cwd": str(project)},
        {"type": "message", "timestamp": "2026-10-27T10:00:00.000Z",
         "message": {"role": "user", "content": "继续把 alpha 的 README 写完"}},
        {"type": "message", "timestamp": "2026-10-27T10:01:00.000Z",
         "message": {"role": "assistant",
                     "content": [{"type": "text", "text": "alpha 的测试已经过了"}]}},
        {"type": "message", "timestamp": "2026-10-27T10:02:00.000Z",
         "message": {"role": "user", "content": "另一个项目的事"}},
    ]
    (sdir / "2026-10-27T10-00-00-000Z_x.jsonl").write_text(
        "\n".join(json.dumps(x, ensure_ascii=False) for x in lines) + "\n",
        encoding="utf-8")
    return sdir


def decisions_lines(data_dir: Path) -> list[dict]:
    out = []
    for f in sorted((data_dir / "grounding").glob("decisions-*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(json.loads(line))
    return out


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        projects = tmp / "vibe"
        projects.mkdir()
        alpha = make_project(projects, "alpha", "# Alpha\nAlphaSecret：做 alpha 的事\n第二行\n",
                             {"plan.md": "计划：先读 README，再跑 alpha 测试\n"})
        beta = make_project(projects, "beta", "# Beta\nBetaSecret：做 beta 的事\n")
        sessions = tmp / "sessions"
        make_sessions(sessions, alpha)

        # —— 1. 标题 → 本机项目 ——
        resolver = ProjectResolver(projects)
        check(resolver.resolve(f"zsh · {alpha}") == str(alpha.resolve()),
              "标题里的路径应解析到项目")
        check(resolver.resolve(str(alpha)) == str(alpha.resolve()),
              "裸路径应解析")
        check(resolver.resolve("zsh · /home/z/vibe/does-not-exist-xyz") is None,
              "不存在的路径不解析")
        check(resolver.resolve("zsh · 完全未知的东西") is None, "未知标题不解析")
        check(resolver.resolve(None) is None, "空标题不解析")
        check(ProjectResolver().root == Path.home() / "vibe",
              "默认项目根是 $HOME/vibe（可移植，不写死 /home/z）")
        row = {"meta": json.dumps({"to": f"zsh · {alpha}"}, ensure_ascii=False)}
        check(title_from_observation(row) == f"zsh · {alpha}",
              "observations.meta.to 应成为标题")
        check(title_from_observation({"meta": "{}"}) is None, "无标题返回 None")

        # —— 2. 材料只来自被解析项目；locator/fetched_at/sha256 ——
        fetcher = MaterialFetcher(projects_root=projects, sessions_root=sessions)
        mats = fetcher.fetch(str(alpha))
        kinds = {m.kind for m in mats}
        check("repo_readme" in kinds, f"应取到 README 材料: {kinds}")
        check("repo_doc" in kinds, f"应取到 docs 材料: {kinds}")
        check("git_log" in kinds, f"应取到 git log: {kinds}")
        check("session" in kinds, f"应取到会话材料: {kinds}")
        check(all(m.project == str(alpha.resolve()) for m in mats),
              "所有材料的 project 必须是被解析项目")
        check(len({m.id for m in mats}) == len(mats), "材料 id 在一次尝试内唯一")
        readme_mat = next(m for m in mats if m.kind == "repo_readme")
        check("AlphaSecret" in readme_mat.excerpt, "README 摘要来自 alpha")
        check("BetaSecret" not in "\n".join(m.excerpt for m in mats),
              "不得混入别的项目正文")
        check(readme_mat.locator.startswith("README.md#L1-"), readme_mat.locator)
        check(readme_mat.fetched_at.endswith("Z"), "fetched_at 是 UTC ISO")
        check(readme_mat.sha256 == sha(readme_mat.excerpt), "sha256 是摘要的重算值")
        beta_mats = fetcher.fetch(str(beta))
        check("AlphaSecret" not in "\n".join(m.excerpt for m in beta_mats),
              "取 beta 时不得混入 alpha 正文")
        check(all(m.project == str(beta.resolve()) for m in beta_mats),
              "beta 的材料 project 全是 beta")
        sess = [m for m in mats if m.kind == "session"]
        check(sess and all("alpha" in m.excerpt.lower() for m in sess),
              "会话片段必须提到项目")
        check(all(m.locator.split("@")[-1].endswith("Z") for m in sess),
              "会话 locator 带时间戳")

        # —— 3. 无项目 / 无材料 → 沉默且模型 0 次 ——
        store = Store(tmp / "db1.sqlite")
        data1 = tmp / "data1"
        fake = FakeModel([draft([readme_mat.id])])
        eng0 = GroundingEngine(store, data1, fetcher=fetcher,
                               commentator=Commentator(fake, log=None),
                               resolver=resolver, log=None, now_fn=lambda: 1000.0)
        d = eng0.maybe_ground("zsh · 完全未知")
        check(d["outcome"] == "silence" and d["reason"] == "no_project",
              f"无项目应沉默: {d}")
        check(len(fake.calls) == 0, "无项目时模型调用必须为 0 次")
        empty = projects / "empty"
        empty.mkdir()
        d = eng0.maybe_ground(str(empty), project=str(empty))
        check(d["outcome"] == "silence" and d["reason"] == "no_material",
              f"无材料应沉默: {d}")
        check(len(fake.calls) == 0, "无材料时模型调用必须为 0 次")
        check(store.conn.execute("SELECT count(*) c FROM expressions").fetchone()["c"] == 0,
              "沉默不得写 expressions")

        # —— 4. 伪造引用被拒 ——
        fake = FakeModel([draft(["mat:does-not-exist"])])
        eng = GroundingEngine(store, data1, fetcher=fetcher,
                              commentator=Commentator(fake, log=None),
                              resolver=resolver, log=None, now_fn=lambda: 1001.0)
        d = eng.maybe_ground(f"zsh · {alpha}")
        check(d["outcome"] == "silence" and d["reason"] == "unknown_citation",
              f"伪造引用应沉默: {d}")
        check(len(fake.calls) == 1, "有材料时才调用模型")
        check(store.conn.execute("SELECT count(*) c FROM expressions").fetchone()["c"] == 0,
              "伪造引用不得落库")

        # —— 5. 跨项目引用被拒（题面项目 A，引用项目 B 的材料）——
        mat_beta = Material(id="repo_readme:beta", kind="repo_readme",
                            project=str(beta.resolve()), locator="README.md#L1-2",
                            fetched_at="2026-10-27T00:00:00Z",
                            excerpt="BetaSecret", sha256=sha("BetaSecret"))
        mixed = [readme_mat, mat_beta]
        comm = Commentator(FakeModel([draft([mat_beta.id])]), log=None)
        out = comm.compose(f"zsh · {alpha}", str(alpha.resolve()), mixed)
        check(out["outcome"] == "silence" and out["reason"] == "cross_project",
              f"引用别项目应沉默: {out}")
        out = comm.compose(f"zsh · {alpha}", str(alpha.resolve()),
                           [readme_mat, mat_beta])
        check(out["outcome"] == "silence", "同一条仍须拒绝")
        bad = validate_draft(json.loads(draft([readme_mat.id],
                                              thesis_project=str(beta.resolve()))),
                             str(alpha.resolve()), [readme_mat])
        check(bad[0] is None and bad[1] == "thesis_project_mismatch",
              f"题面项目不符应拒绝: {bad}")
        bad = validate_draft({"thesis": "x", "content": "y", "citations": []},
                             str(alpha.resolve()), [readme_mat])
        check(bad[0] is None and bad[1] == "uncited", f"空引用应拒绝: {bad}")

        # —— 6. 合法表达落库，source_refs 三件齐全 ——
        fake = FakeModel([draft([readme_mat.id])])
        eng = GroundingEngine(store, data1, fetcher=fetcher,
                              commentator=Commentator(fake, log=None),
                              resolver=resolver, log=None, now_fn=lambda: 1002.0)
        obs_id = store.insert("observations", ts_wall=1002, ts_mono=1.0, sensor="screen",
                              kind="screen.notable",
                              meta={"to": f"zsh · {alpha}"})
        d = eng.maybe_ground(f"zsh · {alpha}", obs_id=obs_id, moment_id="m-speak")
        check(d["outcome"] == "speak" and d["reason"] == "ok", f"合法材料应说出: {d}")
        check(d["thesis"] and str(alpha.resolve()) == d["project"], "题面解析到项目")
        check(d["citations"] == [readme_mat.id], "决策记录引用")
        exprs = store.conn.execute("SELECT * FROM expressions ORDER BY id").fetchall()
        check(len(exprs) == 1, "应落一条 expressions")
        row = exprs[0]
        check(row["kind"] == "speak" and row["content"] == d["content"], "正文一致")
        refs = json.loads(row["source_refs"])
        mat_refs = [r for r in refs if r.get("table") == "materials"]
        check(len(mat_refs) == 1, "每条引用一条 source_refs")
        ref = mat_refs[0]
        for key in ("id", "locator", "fetched_at", "sha256", "project"):
            check(key in ref, f"source_refs 缺 {key}")
        check(ref["locator"] == readme_mat.locator, "locator 原样保留")
        check(ref["sha256"] == readme_mat.sha256, "sha256 原样保留")
        check(any(r.get("table") == "observations" and r.get("id") == obs_id
                  for r in refs), "触发观察也应回指")
        meta = json.loads(row["meta"])
        check(meta["thesis"] == d["thesis"] and meta["gate"] == "speak",
              "meta 记录题面与闸门结果")

        # —— 7. 决策 JSONL 同时记录 speak 与 silence ——
        lines = decisions_lines(data1)
        outcomes = [x["outcome"] for x in lines]
        check("speak" in outcomes and "silence" in outcomes,
              f"决策日志应同时有说与沉默: {outcomes}")
        check(all("materials" in x and "ts" in x and "reason" in x for x in lines),
              "每条决策都有 ts/reason/materials")
        silenced = [x for x in lines if x["outcome"] == "silence"]
        check(any(x["reason"] == "no_material" for x in silenced), "记录 no_material")
        check(any(x["reason"] == "unknown_citation" for x in silenced),
              "记录引用被拒原因")

        # —— 8. m0pp_eval：干净退出 0；篡改一条退出 1 ——
        clean = tmp / "clean.jsonl"
        speak_decision = next(x for x in lines if x["outcome"] == "speak")
        clean.write_text(json.dumps(speak_decision, ensure_ascii=False) + "\n",
                         encoding="utf-8")
        r = subprocess.run([sys.executable, str(ROOT / "scripts/m0pp_eval.py"),
                            "--decisions", str(clean)],
                           capture_output=True, text=True)
        check(r.returncode == 0, f"干净决策应退出 0: {r.stdout} {r.stderr}")
        check("有据率=100.0%" in r.stdout, r.stdout)
        check("无材料出口数=0" in r.stdout, r.stdout)
        check("错题数（跨项目引用）=0" in r.stdout, r.stdout)
        check("题面正确率=N/A" in r.stdout, "无 labels 不得伪造题面正确率")
        tampered = dict(speak_decision)
        tampered["citations"] = ["mat:does-not-exist"]
        badf = tmp / "bad.jsonl"
        badf.write_text(json.dumps(tampered, ensure_ascii=False) + "\n",
                        encoding="utf-8")
        r = subprocess.run([sys.executable, str(ROOT / "scripts/m0pp_eval.py"),
                            "--decisions", str(badf)],
                           capture_output=True, text=True)
        check(r.returncode == 1, f"篡改引用应退出 1: {r.stdout}")
        forged = json.loads(json.dumps(speak_decision))
        for m in forged["materials"]:
            if m["id"] in forged["citations"]:
                m["excerpt"] = "伪造的正文但保留旧哈希"
        forgef = tmp / "forged.jsonl"
        forgef.write_text(json.dumps(forged, ensure_ascii=False) + "\n",
                          encoding="utf-8")
        r = subprocess.run([sys.executable, str(ROOT / "scripts/m0pp_eval.py"),
                            "--decisions", str(forgef)],
                           capture_output=True, text=True)
        check(r.returncode == 1, f"篡改正文/哈希应退出 1: {r.stdout}")
        nod = tmp / "no-decisions.jsonl"
        nod.write_text("", encoding="utf-8")
        r = subprocess.run([sys.executable, str(ROOT / "scripts/m0pp_eval.py"),
                            "--decisions", str(nod)], capture_output=True, text=True)
        check(r.returncode == 2, "没有决策应退出 2（不能确立）")

        # —— 9. 抽样表生成（不足 50 全列）——
        sheet = tmp / "sheet.md"
        r = subprocess.run([sys.executable, str(ROOT / "scripts/m0pp_sample.py"),
                            "--decisions", str(clean), "--out", str(sheet)],
                           capture_output=True, text=True)
        check(r.returncode == 0 and sheet.is_file(), f"抽样表应生成: {r.stderr}")
        text = sheet.read_text(encoding="utf-8")
        check("正确/错误" in text, "抽样表应有空的人工判定列")
        check(speak_decision["moment_id"] in text, "抽样表含 moment id")
        check(speak_decision["thesis"] in text, "抽样表含题面")
        check("不足 50" in text, "不足 50 条应明说")
        check("README.md#L1-" in text, "默认表仍列材料 locator")
        check("AlphaSecret" not in text, "默认表不得含材料正文（只列 locator）")
        check("真实 pi 会话用户消息" in text and "source=session" in text,
              "表头须说明批量触发是真实会话消息、不是伪造时刻")
        sheet_full = tmp / "sheet-full.md"
        r = subprocess.run([sys.executable, str(ROOT / "scripts/m0pp_sample.py"),
                            "--decisions", str(clean), "--out", str(sheet_full),
                            "--full"], capture_output=True, text=True)
        check(r.returncode == 0 and sheet_full.is_file(),
              f"--full 详细表应生成: {r.stderr}")
        full_text = sheet_full.read_text(encoding="utf-8")
        check("AlphaSecret" in full_text, "--full 表含材料正文摘要")
        longdec = dict(speak_decision, title="zsh · " + "长" * 300)
        longf = tmp / "long.jsonl"
        longf.write_text(json.dumps(longdec, ensure_ascii=False) + "\n",
                         encoding="utf-8")
        longsheet = tmp / "long-sheet.md"
        r = subprocess.run([sys.executable, str(ROOT / "scripts/m0pp_sample.py"),
                            "--decisions", str(longf), "--out", str(longsheet)],
                           capture_output=True, text=True)
        check(r.returncode == 0, f"长标题抽样表应生成: {r.stderr}")
        long_text = longsheet.read_text(encoding="utf-8")
        check("长" * 300 not in long_text and "长" * 121 not in long_text,
              "默认表把标题截断到约 120 字符")

        # —— 10. demo --no-model 确定性（只取材料，不调模型）——
        demo_data = tmp / "demo-data"
        r = subprocess.run([sys.executable, str(ROOT / "scripts/m0pp_demo.py"),
                            "--title", f"zsh · {alpha}", "--project", str(alpha),
                            "--data-dir", str(demo_data), "--no-model"],
                           capture_output=True, text=True, cwd=ROOT)
        check(r.returncode == 0, f"demo 应退出 0: {r.stdout} {r.stderr}")
        check("model_disabled" in r.stdout, "无模型时沉默原因是 model_disabled")
        check(len(decisions_lines(demo_data)) == 1, "demo 产出一条决策")
        batch_data = tmp / "demo-batch"
        r = subprocess.run([sys.executable, str(ROOT / "scripts/m0pp_demo.py"),
                            "--moments", "1",
                            "--projects-root", str(projects),
                            "--sessions-root", str(sessions),
                            "--data-dir", str(batch_data), "--no-model"],
                           capture_output=True, text=True, cwd=ROOT)
        check(r.returncode == 0, f"批量 demo 应退出 0: {r.stdout} {r.stderr}")
        batch = decisions_lines(batch_data)
        check(len(batch) == 1 and batch[0]["project"] == str(alpha.resolve()),
              f"批量 demo 必须用传入的 projects-root 映射项目: {batch}")
        check(batch[0]["source"] == "session", "批量时刻来源是 session")

        # —— 11. daemon 挂点：新 small 事实触发 + 节流 + 游标不重放 ——
        store2 = Store(tmp / "db2.sqlite")
        data2 = tmp / "data2"
        clock = [5000.0]
        fake = FakeModel([draft(["m1"]), draft(["m1"])])
        eng2 = GroundingEngine(store2, data2, fetcher=fetcher,
                               commentator=Commentator(fake, log=None),
                               resolver=resolver, min_interval=300.0, log=None,
                               now_fn=lambda: clock[0])
        o1 = store2.insert("observations", ts_wall=4999, ts_mono=1.0,
                           sensor="screen", kind="screen.notable",
                           meta={"to": f"zsh · {alpha}"})
        store2.insert("messages", level="small", ts_start=4999, ts_end=4999,
                      content="改了 README", type="diff", obs_ids=[o1])
        d = eng2.maybe_ground_pending()
        check(d is not None and d["outcome"] == "speak", f"新事实应触发一次: {d}")
        check(d["source"] == "observation" and d["source_obs"] == o1,
              "决策记录触发来源与观察")
        clock[0] += 1.0
        check(eng2.maybe_ground_pending() is None, "节流窗内不重复取材料")
        clock[0] += 400.0
        check(eng2.maybe_ground_pending() is None, "同一行不得重放（游标）")
        o2 = store2.insert("observations", ts_wall=clock[0], ts_mono=1.0,
                           sensor="screen", kind="screen.notable",
                           meta={"to": f"zsh · {alpha}"})
        store2.insert("messages", level="small", ts_start=clock[0],
                      ts_end=clock[0], content="又改了一行", type="diff",
                      obs_ids=[o2])
        d = eng2.maybe_ground_pending()
        check(d is not None and d["source_obs"] == o2, "新的一行才允许下一次尝试")
        check(len(fake.calls) == 2, "两次尝试正好两次模型调用")
        store2.close()

        # —— 12. 触发标题：标题在第二帧 / 退到近前 window_switch / 无标题不调用模型 ——
        store3 = Store(tmp / "db3.sqlite")
        data3 = tmp / "data3"
        clock3 = [9000.0]
        fake = FakeModel([draft(["m1"]), draft(["m1"])])
        eng3 = GroundingEngine(store3, data3, fetcher=fetcher,
                               commentator=Commentator(fake, log=None),
                               resolver=resolver, min_interval=300.0, log=None,
                               now_fn=lambda: clock3[0])
        # (a) 标题在第二个 obs id 上（第一帧没有）
        plain = store3.insert("observations", ts_wall=9000, ts_mono=1.0,
                              sensor="screen", kind="screen.frame",
                              meta={"app": "zsh"})
        titled = store3.insert("observations", ts_wall=9000, ts_mono=1.0,
                               sensor="screen", kind="window_switch",
                               meta={"to": f"zsh · {alpha}"})
        store3.insert("messages", level="small", ts_start=9000, ts_end=9000,
                      content="diff a", type="diff", obs_ids=[plain, titled])
        d = eng3.maybe_ground_pending()
        check(d is not None and d["source_obs"] == titled,
              f"标题在第二个 obs id 时应使用它: {d}")
        check(d["title"] == f"zsh · {alpha}", "题面来自第二帧的 meta.to")
        # (b) 行内都无标题：退到该行时间前最近一条带 meta.to 的观察
        clock3[0] += 2000.0
        p1 = store3.insert("observations", ts_wall=clock3[0], ts_mono=1.0,
                           sensor="screen", kind="screen.frame", meta={"app": "zsh"})
        p2 = store3.insert("observations", ts_wall=clock3[0], ts_mono=1.0,
                           sensor="screen", kind="screen.frame", meta={"app": "zsh"})
        near = store3.insert("observations", ts_wall=clock3[0] - 120, ts_mono=1.0,
                             sensor="screen", kind="window_switch",
                             meta={"to": f"zsh · {alpha}"})
        store3.insert("messages", level="small", ts_start=clock3[0],
                      ts_end=clock3[0], content="diff b", type="diff",
                      obs_ids=[p1, p2])
        d = eng3.maybe_ground_pending()
        check(d is not None and d["source_obs"] == near,
              f"行内无标题时退到近前 window_switch 观察: {d}")
        check(len(fake.calls) == 2, "两次带标题的尝试各调用一次模型")
        # (c) 任何 obs / 近前窗口都没有标题：不尝试、不调用模型
        clock3[0] += 2000.0
        p3 = store3.insert("observations", ts_wall=clock3[0], ts_mono=1.0,
                           sensor="screen", kind="screen.frame", meta={"app": "zsh"})
        store3.insert("messages", level="small", ts_start=clock3[0],
                      ts_end=clock3[0], content="diff c", type="diff",
                      obs_ids=[p3])
        before = len(fake.calls)
        before_decisions = len(decisions_lines(data3))
        check(eng3.maybe_ground_pending() is None, "无标题时放弃本次尝试")
        check(len(fake.calls) == before, "无标题时不调用模型")
        check(len(decisions_lines(data3)) == before_decisions,
              "无标题时不写新决策")
        store3.close()

        store.close()

    print(f"ALL PASS（{CHECKS} 条断言）")


if __name__ == "__main__":
    main()
