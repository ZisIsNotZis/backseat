"""M0″ 外部材料表达链：触发 → 题面 → 外部材料 → 带引用的评论 / 沉默。

屏幕像素只当触发器，不是内容源。本模块只做一条链：

  Trigger（窗口/标签标题，或一条新 small 事实里的 observations.meta.to）
    → ProjectResolver（标题里的路径/项目名 → ~/vibe/<项目>）
    → MaterialFetcher（本机仓库 README/docs/git + 该项目的 pi 会话记录）
    → Commentator（只把题面与编号材料交给模型；引用必须可解析、同项目）
    → expressions（source_refs 带 locator/fetched_at/sha256）或沉默

每一尝试（说与不说）都追加一行到 <data_dir>/grounding/decisions-YYYY-MM-DD.jsonl，
它是判官（scripts/m0pp_eval.py）的输入。没有材料时不调用模型；引用不合法时整条沉默。
不新增 SQLite 表：表达仍住 `expressions`，引用仍住 `source_refs`/`meta`。

逐调用计量：每次确实发生的模型调用（purpose `grounding:comment`）向 `metrics` 落一行、向
`<data_dir>/trajectory/` 记一条，决策里带 `usage`；没调模型（无项目/无材料/模型关闭）或调用
失败时不落行、不伪造 usage。
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from .clock import Stamp
from .model import ModelError
from .persona import MOOD_COLORS

PROJECTS_ROOT = Path.home() / "vibe"
SESSIONS_ROOT = Path.home() / ".pi" / "agent" / "sessions"
DEFAULT_MIN_INTERVAL = 300.0  # 秒；daemon 里两次取材料的最小间隔（config 可覆盖）
MAX_EXCERPT = 1500            # 每条材料的正文字符上限
MAX_DOCS = 6                  # docs/*.md 最多取几份
MAX_SESSION_FILES = 2         # 最近几个会话文件
MAX_SESSION_SNIPPETS = 3      # 会话片段条数上限
MAX_SNIPPET = 400             # 单条会话片段字符上限
TITLE_FALLBACK_WINDOW = 1800.0  # 秒；small 行无标题时向前找 window_switch 标题的时限

_MOODS = set(MOOD_COLORS)


def _utc_iso(ts: float | None = None) -> str:
    dt = datetime.fromtimestamp(ts if ts is not None else time.time(), tz=timezone.utc)
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _inside(path: Path, root: Path) -> bool:
    """path 是否落在 root 之内（解析符号链接后）。"""
    try:
        path.resolve().is_relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


@dataclass(frozen=True)
class Material:
    """一份外部材料。id 在一次尝试内唯一；locator 是事后可复查的位置。"""

    id: str
    kind: str          # repo_readme|repo_doc|git_log|git_status|git_diff|session
    project: str
    locator: str       # 相对路径#Lx-y / git:<cmd> / 会话文件@时间戳
    fetched_at: str    # UTC ISO
    excerpt: str
    sha256: str

    def to_dict(self) -> dict:
        return {"id": self.id, "kind": self.kind, "project": self.project,
                "locator": self.locator, "fetched_at": self.fetched_at,
                "excerpt": self.excerpt, "sha256": self.sha256}


@dataclass(frozen=True)
class Trigger:
    title: str | None
    obs_id: int | None = None
    source: str = "cli"  # cli|observation|session


def title_from_observation(row) -> str | None:
    """从 observations 行取标题：meta.to（屏幕窗口/标签标题）优先，其次 meta.title。"""
    try:
        meta = json.loads(row["meta"] or "{}")
    except (ValueError, TypeError):
        return None
    if not isinstance(meta, dict):
        return None
    for key in ("to", "title", "window_title"):
        v = meta.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


class ProjectResolver:
    """标题 → 本机项目路径。解析不出就返回 None（不猜）。"""

    def __init__(self, root: Path | str = PROJECTS_ROOT) -> None:
        self.root = Path(root).expanduser()

    def _accept(self, raw: str) -> str | None:
        p = Path(raw).expanduser()
        if not p.is_absolute():
            return None
        try:
            p = p.resolve()
        except OSError:
            return None
        if p.is_dir() and _inside(p, self.root):
            return str(p)
        return None

    def resolve(self, title: str | None) -> str | None:
        if not title:
            return None
        # 1) 标题里的绝对/家目录路径：`zsh · ~/vibe/backseat`
        for m in re.finditer(r"(?:~/|/)[^\s·|,;:()\[\]<>\"']+", title):
            got = self._accept(m.group(0))
            if got:
                return got
        # 2) 标题里只有项目名（`zsh · backseat`）：只在名字恰是 root 下的目录时接受
        for tok in re.findall(r"[A-Za-z0-9_.\-]+", title):
            cand = self.root / tok
            if cand.is_dir():
                return str(cand.resolve())
        return None


class MaterialFetcher:
    """从本机仓库（只读项目内文件 + git）与该项目 pi 会话取材料。"""

    def __init__(self, projects_root: Path | str = PROJECTS_ROOT,
                 sessions_root: Path | str = SESSIONS_ROOT,
                 max_excerpt: int = MAX_EXCERPT) -> None:
        self.projects_root = Path(projects_root).expanduser()
        self.sessions_root = Path(sessions_root).expanduser()
        self.max_excerpt = max_excerpt

    # —— 仓库 ——
    @staticmethod
    def _readme(project: Path) -> Path | None:
        for name in ("README.md", "README", "README.zh-CN.md"):
            p = project / name
            if p.is_file() and _inside(p, project):
                return p
        found = sorted(project.glob("README*"))
        for p in found:
            if p.is_file() and _inside(p, project):
                return p
        return None

    def _file_material(self, project: Path, path: Path, kind: str) -> Material | None:
        if not _inside(path, project) or not path.is_file():
            return None
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        lines = text.splitlines()
        used: list[str] = []
        total = 0
        for line in lines:
            if used and total + len(line) + 1 > self.max_excerpt:
                break
            used.append(line)
            total += len(line) + 1
        excerpt = "\n".join(used)
        if not excerpt.strip():
            return None
        rel = path.relative_to(project.resolve()).as_posix()
        locator = f"{rel}#L1-{max(1, len(used))}"
        return Material(id=f"{kind}:{locator}", kind=kind, project=str(project),
                        locator=locator, fetched_at=_utc_iso(), excerpt=excerpt,
                        sha256=_sha256(excerpt))

    def _git(self, project: Path, args: list[str], kind: str) -> Material | None:
        try:
            out = subprocess.run(["git", "-C", str(project), *args],
                                 capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.TimeoutExpired):
            return None
        if out.returncode != 0:
            return None
        excerpt = out.stdout.strip()
        if not excerpt:
            return None
        if len(excerpt) > self.max_excerpt:
            excerpt = excerpt[: self.max_excerpt]
        locator = "git:" + " ".join(args)
        return Material(id=f"{kind}:{locator}", kind=kind, project=str(project),
                        locator=locator, fetched_at=_utc_iso(), excerpt=excerpt,
                        sha256=_sha256(excerpt))

    # —— pi 会话 ——
    def session_dir_for(self, project: str | Path) -> Path:
        """`~/vibe/backseat` → `--home-z-vibe-backseat--`。"""
        slug = str(Path(project).resolve()).strip("/").replace("/", "-")
        return self.sessions_root / f"--{slug}--"

    @staticmethod
    def _message_text(content) -> str:
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

    def _session_materials(self, project: Path) -> list[Material]:
        sdir = self.session_dir_for(project)
        if not sdir.is_dir() or not _inside(sdir, self.sessions_root):
            return []
        files = sorted((p for p in sdir.glob("*.jsonl") if p.is_file()),
                       key=lambda p: p.stat().st_mtime, reverse=True)[:MAX_SESSION_FILES]
        needles = [project.name, str(project)]
        snippets: list[tuple[str, Path, str]] = []
        fallback: list[tuple[str, Path, str]] = []
        for f in files:
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
                # 会话里人说的话与工具回显都算记录；只保留提到本项目的片段。
                if msg.get("role") not in ("user", "assistant", "toolResult"):
                    continue
                text = " ".join(self._message_text(msg.get("content")).split())
                if not text:
                    continue
                if not any(n and n in text for n in needles):
                    continue
                item = (str(d.get("timestamp") or ""), f, text[:MAX_SNIPPET])
                # 人的话/助手的话优先；没有时才退到工具回显（仍是提到本项目的记录）。
                (fallback if msg.get("role") == "toolResult" else snippets).append(item)
        chosen = snippets if snippets else fallback
        chosen.sort(key=lambda x: x[0], reverse=True)
        out: list[Material] = []
        for ts, f, text in chosen[:MAX_SESSION_SNIPPETS]:
            locator = f"{f}@{ts}"
            out.append(Material(id=f"session:{locator}", kind="session",
                                project=str(project), locator=locator,
                                fetched_at=_utc_iso(), excerpt=text,
                                sha256=_sha256(text)))
        return out

    def fetch(self, project: str | None) -> list[Material]:
        if not project:
            return []
        p = Path(project).expanduser()
        if not p.is_dir():
            return []
        p = p.resolve()
        mats: list[Material] = []
        readme = self._readme(p)
        if readme:
            m = self._file_material(p, readme, "repo_readme")
            if m:
                mats.append(m)
        docs = sorted((p / "docs").glob("*.md"))
        added = 0
        for d in docs:
            if added >= MAX_DOCS:
                break
            m = self._file_material(p, d, "repo_doc")
            if m:
                mats.append(m)
                added += 1
        for args, kind in ((["log", "-n", "5", "--oneline"], "git_log"),
                           (["status", "--short"], "git_status"),
                           (["diff", "--stat"], "git_diff")):
            m = self._git(p, args, kind)
            if m:
                mats.append(m)
        mats.extend(self._session_materials(p))
        # 一次尝试内用短 id（m1, m2, …）：模型引用时只需原样照抄，长 locator 仍留字段里可复查。
        return [replace(m, id=f"m{i}") for i, m in enumerate(mats, 1)]


# —— 评论（只基于材料）——


def parse_comment_json(text: str) -> dict | None:
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.strip("`")
        if t.startswith("json"):
            t = t[4:]
    try:
        d = json.loads(t)
    except json.JSONDecodeError:
        return None
    return d if isinstance(d, dict) else None


def normalize_thesis(raw, default_project: str) -> tuple[str, str]:
    """题面归一为 (一句话, 项目)。模型可给字符串，也可给 {project,text}。"""
    if isinstance(raw, dict):
        text = raw.get("text") or raw.get("thesis") or raw.get("one_sentence") or ""
        project = raw.get("project") or default_project
        return str(text).strip(), str(project).strip() or default_project
    return str(raw or "").strip(), default_project


def validate_draft(draft: dict, project: str,
                   materials: list[Material]) -> tuple[dict | None, str]:
    """校验模型草稿；通过返回归一后的字段，否则 (None, 沉默原因)。"""
    by_id = {m.id: m for m in materials}
    thesis_text, thesis_project = normalize_thesis(draft.get("thesis"), project)
    if not thesis_text:
        return None, "no_thesis"
    if thesis_project != project:
        return None, "thesis_project_mismatch"
    citations = draft.get("citations")
    if not isinstance(citations, list):
        citations = []
    citations = [str(c) for c in citations if isinstance(c, str) and c.strip()]
    if not citations:
        return None, "uncited"
    for cid in citations:
        if cid not in by_id:
            return None, "unknown_citation"
        if by_id[cid].project != thesis_project:
            return None, "cross_project"
    content = draft.get("content")
    if not isinstance(content, str) or not content.strip():
        return None, "empty_content"
    mood = draft.get("mood") if draft.get("mood") in _MOODS else "neutral"
    sal = draft.get("salience")
    sal = float(sal) if isinstance(sal, (int, float)) and 0 <= sal <= 1 else 0.5
    return {"thesis": thesis_text, "thesis_project": thesis_project,
            "content": content.strip(), "citations": citations,
            "mood": mood, "salience": sal}, "speak"


class Commentator:
    """把题面 + 编号材料交给模型；解析、校验、失败即沉默。

    每次成功的模型调用都记进 trajectory（purpose `grounding:comment`，与 persona 同形）；
    返回的 result 带 `usage`（未调用/调用失败时为 None），引擎据此落 metrics 一行。
    """

    PURPOSE = "grounding:comment"

    def __init__(self, model, log=print, trajectory=None) -> None:
        self.model = model
        self.log = log
        self.trajectory = trajectory

    def model_name(self) -> str:
        return str(getattr(self.model, "model", "") or "unknown")

    def _record(self, messages: list[dict], text: str, usage: dict) -> None:
        """仅成功调用才落 trajectory；usage 原样交给 Trajectory（它按同一形状取值）。"""
        if self.trajectory is None:
            return
        self.trajectory.record(self.PURPOSE, self.model_name(), messages, text, usage)

    @staticmethod
    def _silence(reason: str, thesis: str | None = None,
                 usage: dict | None = None) -> dict:
        out = {"outcome": "silence", "reason": reason, "thesis": thesis,
               "content": None, "citations": [], "mood": None, "salience": None,
               "usage": usage}
        return out

    def _prompt(self, title: str | None, project: str,
                materials: list[Material]) -> str:
        lines = [
            "你在给一个人当前的工作写一句评论。你只能用下面编号材料里的信息，",
            "不得补充材料之外的事实；没有可说的就沉默。",
            "",
            f"题面（触发）：{title or project}",
            f"项目：{project}",
            "",
            "材料：",
        ]
        for i, m in enumerate(materials, 1):
            lines.append(f"[{i}] id={m.id} kind={m.kind} locator={m.locator}")
            lines.append("    " + m.excerpt.replace("\n", "\n    "))
        lines += [
            "",
            "只输出一个 JSON 对象，无其他文字：",
            '{"thesis":"一句话：他在哪个项目、哪一步",',
            ' "content":"一句中文评论，只基于所引材料",',
            ' "citations":["m1", "…至少一条，原样照抄材料 id"],',
            ' "mood":"neutral|focused|happy|frustrated|mocking|guilty|anxious|excited|shocked|tired",',
            ' "salience":0.0, "silence":false}',
            "- thesis 与 content 必须与 citations 指向的材料一致；引用别的项目的材料即错。",
            "- 没话说就输出 {\"silence\": true}。",
        ]
        return "\n".join(lines)

    def compose(self, title: str | None, project: str,
                materials: list[Material]) -> dict:
        """返回 {outcome, reason, thesis, content, citations, mood, salience, usage}。

        `usage` 只有真的调过模型（且调用成功返回）时才不是 None；未调用/调用失败时为 None。
        """
        if not materials:
            return self._silence("no_material")
        if self.model is None:
            return self._silence("model_disabled")
        messages = [{"role": "user", "content": self._prompt(title, project, materials)}]
        try:
            text, usage = self.model.chat(messages)
        except ModelError:
            return self._silence("model_error")
        except Exception:  # 注入的测试模型可能抛任意异常；对外仍是沉默
            return self._silence("model_error")
        # 调用已发生（且有 usage）：此后每条出口都带 usage，不虚报、不丢计量。
        self._record(messages, text, usage)
        draft = parse_comment_json(text)
        if draft is None:
            return self._silence("parse_error", usage=usage)
        if draft.get("silence"):
            return self._silence("model_silence", usage=usage)
        thesis_text, _ = normalize_thesis(draft.get("thesis"), project)
        normalized, reason = validate_draft(draft, project, materials)
        if normalized is None:
            # 被拒的草稿不留内容/引用，但保留模型提出的题面供人判“它有没有认对题”。
            return self._silence(reason, thesis=thesis_text or None, usage=usage)
        return {"outcome": "speak", "reason": "ok", **normalized, "usage": usage}


class GroundingEngine:
    """一次触发走完整条链，落库或沉默，并把每个尝试写进决策 JSONL。

    每次**确实发生的**模型调用落 `metrics` 一行（purpose `grounding:comment`）；没有材料/
    没有项目时根本没调模型，因此不落行、不编造 usage。
    """

    METRIC_PURPOSE = Commentator.PURPOSE

    def __init__(self, store, data_dir: str | Path,
                 fetcher: MaterialFetcher | None = None,
                 commentator: Commentator | None = None,
                 resolver: ProjectResolver | None = None,
                 min_interval: float = DEFAULT_MIN_INTERVAL,
                 log=print, now_fn=None) -> None:
        self.store = store
        self.data_dir = Path(data_dir).expanduser()
        self.decisions_dir = self.data_dir / "grounding"
        self.fetcher = fetcher or MaterialFetcher()
        self.commentator = commentator
        self.resolver = resolver or ProjectResolver()
        self.min_interval = min_interval
        self.log = log
        self.now_fn = now_fn or time.time
        self.stats = {"attempts": 0, "speak": 0, "silence": 0}

    # —— 计量 ——
    def _record_metrics(self, wall: float, usage: dict) -> None:
        """一次实际模型调用 → metrics 一行（与 persona 同形状）。调用方只在实际调用后调。"""
        model = ""
        if self.commentator is not None:
            model = self.commentator.model_name()
        self.store.insert(
            "metrics", ts_wall=wall, ts_mono=Stamp.now().mono,
            purpose=self.METRIC_PURPOSE, model=model or None,
            tokens_in=usage.get("in", 0), tokens_out=usage.get("out", 0),
            cached_tokens=usage.get("cached", 0),
            latency_ms=usage.get("latency_ms", 0))

    # —— 持久化 ——
    def _append_decision(self, decision: dict) -> None:
        self.decisions_dir.mkdir(parents=True, exist_ok=True)
        day = datetime.fromtimestamp(decision["ts_wall"], tz=timezone.utc).strftime("%Y-%m-%d")
        path = self.decisions_dir / f"decisions-{day}.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(decision, ensure_ascii=False) + "\n")

    def _store_expression(self, wall: float, title: str | None, project: str,
                          materials: list[Material], result: dict,
                          obs_id: int | None) -> int:
        cited = [m for m in materials if m.id in result["citations"]]
        source_refs = [{"table": "materials", "id": m.id, "locator": m.locator,
                        "fetched_at": m.fetched_at, "sha256": m.sha256,
                        "project": m.project} for m in cited]
        if obs_id is not None:
            source_refs.insert(0, {"table": "observations", "id": obs_id})
        return self.store.insert(
            "expressions", ts_wall=wall, kind="speak", content=result["content"],
            channel="grounded", mood=result["mood"], salience=result["salience"],
            source_refs=source_refs,
            status="sent",
            meta={"thesis": result["thesis"], "material_count": len(materials),
                  "gate": "speak", "project": project, "title": title})

    # —— 一次尝试 ——
    def maybe_ground(self, title: str | None, obs_id: int | None = None,
                     project: str | None = None, moment_id: str | None = None,
                     source: str = "cli", now: float | None = None) -> dict:
        wall = self.now_fn() if now is None else now
        title = title.strip() if isinstance(title, str) and title.strip() else None
        trigger = Trigger(title=title, obs_id=obs_id, source=source)
        if project is None:
            project = self.resolver.resolve(title)
        materials = self.fetcher.fetch(project) if project else []
        if project is None:
            result = {"outcome": "silence", "reason": "no_project", "thesis": None,
                      "content": None, "citations": [], "mood": None, "salience": None,
                      "usage": None}
        elif not materials:
            result = {"outcome": "silence", "reason": "no_material", "thesis": None,
                      "content": None, "citations": [], "mood": None, "salience": None,
                      "usage": None}
        elif self.commentator is None:
            result = {"outcome": "silence", "reason": "model_disabled", "thesis": None,
                      "content": None, "citations": [], "mood": None, "salience": None,
                      "usage": None}
        else:
            result = self.commentator.compose(title, project, materials)

        usage = result.get("usage")
        if usage is not None:
            self._record_metrics(wall, usage)

        expression_id = None
        if result.get("outcome") == "speak":
            expression_id = self._store_expression(
                wall, title, project, materials, result, obs_id)
        decision = {
            "ts": _utc_iso(wall),
            "ts_wall": wall,
            "moment_id": moment_id,
            "source": source,
            "outcome": result["outcome"],
            "reason": result["reason"],
            "title": trigger.title,
            "project": project,
            "thesis": result.get("thesis"),
            "materials": [m.to_dict() for m in materials],
            "content": result.get("content"),
            "citations": result.get("citations") or [],
            "source_obs": obs_id,
            "expression_id": expression_id,
            "usage": usage,
        }
        self._append_decision(decision)
        self.stats["attempts"] += 1
        self.stats["speak" if decision["outcome"] == "speak" else "silence"] += 1
        if self.log:
            self.log(f"[grounding] {decision['outcome']}/{decision['reason']} "
                     f"project={project} materials={len(materials)}")
        return decision

    # —— daemon 挂点 ——
    def maybe_ground_pending(self, now: float | None = None) -> dict | None:
        """主循环里的低频挂点：有新 small 事实、且过了最小间隔，才尝试一次。

        游标住 state.grounding_cursor，处理过的行不会重触发；时钟住
        state.last_grounding。没有标题就没有题面，不发起尝试。
        """
        wall = self.now_fn() if now is None else now
        last = self.store.get_state("last_grounding", 0.0) or 0.0
        if wall - last < self.min_interval:
            return None
        cursor = self.store.get_state("grounding_cursor", 0) or 0
        rows = self.store.conn.execute(
            "SELECT * FROM messages WHERE level='small' AND id>? ORDER BY id LIMIT 5",
            (cursor,)).fetchall()
        if not rows:
            return None
        for r in rows:
            self.store.set_state("grounding_cursor", r["id"])
        self.store.set_state("last_grounding", wall)
        latest = rows[-1]
        title, obs_id = self._title_for_row(latest)
        if not title:
            return None
        return self.maybe_ground(title, obs_id=obs_id, source="observation", now=wall)

    @staticmethod
    def _obs_ids(row) -> list[int]:
        try:
            ids = json.loads(row["obs_ids"] or "[]")
        except (ValueError, TypeError):
            return []
        out: list[int] = []
        for i in ids if isinstance(ids, list) else []:
            try:
                out.append(int(i))
            except (TypeError, ValueError):
                continue
        return out

    def _title_for_row(self, row) -> tuple[str | None, int | None]:
        """small 行的标题：先扫全行 obs_ids（标题常在 window_switch 帧上，不保证第一帧），
        再退到该行 ts_start 之前、时间有界的最近一条带 meta.to 的观察。

        兜底窗口是 TITLE_FALLBACK_WINDOW（30 分钟）且不早于该行 ts_start；查库只在这段
        时间带上按 ts_wall 索引取，不扫全库。仍无标题就 (None, None)，调用方放弃本次尝试。
        """
        for oid in self._obs_ids(row):
            obs = self.store.conn.execute(
                "SELECT * FROM observations WHERE id=?", (oid,)).fetchone()
            if obs is not None:
                title = title_from_observation(obs)
                if title:
                    return title, oid
        try:
            ts_start = float(row["ts_start"])
        except (TypeError, ValueError, KeyError):
            return None, None
        lo = ts_start - TITLE_FALLBACK_WINDOW
        nearby = self.store.conn.execute(
            "SELECT * FROM observations WHERE ts_wall<=? AND ts_wall>=? "
            "AND meta LIKE '%\"to\"%' ORDER BY ts_wall DESC LIMIT 20",
            (ts_start, lo)).fetchall()
        for obs in nearby:
            title = title_from_observation(obs)
            if title:
                return title, int(obs["id"])
        return None, None
