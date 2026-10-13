"""人格层（M5）：风格归人格，事实归引擎（DESIGN §10.G4）。

Persona 插件「弹幕君」：订阅事实流（importance≥线 / KB命中 / 大事件 / 周期digest），
独立冻结前缀（人设+记忆引擎说明+表达契约），产出表达草稿 {content, mood, salience}。
引擎闸门施加于草稿之后：节流（min_roast_interval）→ 去重（与近期发言重叠≥60% 拦）
→ 沉默权（模型可输出 {"silence": true}）。放行 → expressions 行 → 出口渲染。

出口协议：Outlet(draft) -> status 回执。danmaku 出口 = 子进程 danmaku_engine（stdin
JSON-lines），mood→颜色、salience≥0.85→大字（双行带），未知 mood 降级 neutral。
引擎逐条回执（{"id","status"}）经 ReceiptReader 关联：确认真渲染才 "shown"，否则
"sent"（已投递未确认）——"shown" 语义可信（RUNBOOK 坑 #6）。
"""

from __future__ import annotations

import json
import subprocess
import threading
import time

from .clock import Stamp, fmt_wall
from .model import ModelClient, ModelError
from .trajectory import Trajectory

MOOD_COLORS = {
    "neutral": "#FFFFFF", "focused": "#8AB4FF", "happy": "#7DFF8A",
    "frustrated": "#FF8A5C", "mocking": "#FFD24D", "guilty": "#B39DFF",
    "anxious": "#FF6B6B", "excited": "#4DFFD2", "shocked": "#FF4D9E",
    "tired": "#9E9E9E",
}
LARGE_SALIENCE = 0.85

PERSONA_SYSTEM_ASSISTANT = """你是「弹幕君」：坐在用户人生后座的助手。你的职责是让用户看到他自己看不到的东西。

铁律（违反即沉默）：
1. 禁止复述屏幕上用户自己能看见的内容（剧情/画面/正在做什么）——那是废话
2. 说话的唯一理由 = 信息不对称：你知道而用户不知道的东西。可用素材：
   [洞察候选]（跨时间的重复/间隔/累积/异常）、[KB命中行]（长期知识）、
   [USER_MODEL]（你对他的理解）、[近期事实]（仅作为洞察的依据，不得直接转述）
3. 若没有值得说的话 → {"silence": true}。安静是常态，有意义才开口

表达契约（只输出一个 JSON 对象）：
{"content":"一句话（中文，≤40字；可以有梗，但必须有信息量）",
 "mood":"neutral|focused|happy|frustrated|mocking|guilty|anxious|excited|shocked|tired",
 "salience":0.0~1.0, "silence":false}
- mood 分配：日常陈述用 neutral/focused；只有真有笑点才 mocking；坏消息 frustrated/anxious
- 不做氛围组；不寒暄；不为了说话而说话

记忆引擎说明（固定）：[洞察候选] 是确定性挖掘器从长期记忆中算出的；[KB命中行] 与
USER_MODEL 由记忆引擎注入；[近期事实] 是流水，不是你说的话——非你生成。"""

PERSONA_SYSTEM_COMPANION = """你是「弹幕君」：坐在用户人生后座的 AI 伴侣，看完全程，忍不住评论。

表达契约（只输出一个 JSON 对象）：
{"content":"一句话弹幕（中文，≤40字，有梗）",
 "mood":"neutral|focused|happy|frustrated|mocking|guilty|anxious|excited|shocked|tired",
 "salience":0.0~1.0, "silence":false}
- 事实由引擎注入，你只负责"怎么说"；不编造未提供的事实
- 沉默权：没梗/不值得说就 {"silence": true}——安静是常态

记忆引擎说明（固定）：USER_MODEL=对用户的当前理解；新事实=流水；
[KB命中行]=长期知识自动浮现；expressions尾部=你最近说过什么。均由引擎注入。"""


def parse_draft(text: str) -> dict | None:
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        if t.startswith("json"):
            t = t[4:]
    try:
        d = json.loads(t)
    except json.JSONDecodeError:
        return None
    if not isinstance(d, dict):
        return None
    if d.get("silence"):
        return {"silence": True}
    c = d.get("content")
    if not isinstance(c, str) or not c.strip():
        return None
    mood = d.get("mood") if d.get("mood") in MOOD_COLORS else "neutral"
    sal = d.get("salience")
    sal = sal if isinstance(sal, (int, float)) and 0 <= sal <= 1 else 0.5
    return {"content": c.strip(), "mood": mood, "salience": float(sal)}


class ReceiptReader:
    """按 id 关联引擎逐条回执（后台线程读引擎 stdout 行）。

    引擎每上屏一条带 id 的输入回一行 {"id":N,"status":"shown|skipped"}。非回执行
    （引擎可能打印的 stats 等）忽略。旧引擎不回执 → 等待超时，调用方按"未确认"处理。
    """

    def __init__(self, stream) -> None:
        self._stream = stream
        self._cv = threading.Condition()
        self._receipts: dict = {}
        # 迟到回执有界化：id 由 DanmakuOutlet 单调递增且逐个同步等待，故 wait(N) 一旦
        # 超时，所有 id<=N 的回执都已无意义（不再有人取）。记下已放弃的最大 id，读线程
        # 据此丢弃迟到回执——否则字典会随超时次数无界增长（引擎冷启动 >0.5s 时首条必
        # 触发）。上界 = 在途请求数（实际为 1）。
        self._abandoned_upto = 0
        self._thread = threading.Thread(target=self._read_loop, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _read_loop(self) -> None:
        for line in self._stream:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            mid, status = obj.get("id"), obj.get("status")
            if mid is None or status not in ("shown", "skipped"):
                continue
            with self._cv:
                if mid <= self._abandoned_upto:
                    continue  # 已超时放弃的 id：丢弃迟到回执，防无界滞留
                self._receipts[mid] = status
                self._cv.notify_all()

    def wait(self, mid, timeout: float) -> str | None:
        """等 id 的回执；超时/引擎退出返回 None（调用方按未确认处理）。"""
        deadline = time.monotonic() + timeout
        with self._cv:
            while mid not in self._receipts:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    if mid > self._abandoned_upto:
                        self._abandoned_upto = mid
                    return None
                self._cv.wait(remaining)
            return self._receipts.pop(mid)


def _spawn_engine() -> subprocess.Popen:
    """以 stdout=PIPE 托管引擎，使逐条回执可读（stderr 丢弃）。"""
    return subprocess.Popen(
        ["python3", "-m", "backseat.outlets.danmaku_engine"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, text=True, bufsize=1)


class DanmakuOutlet:
    """弹幕出口：托管 danmaku_engine 子进程（stdin JSON-lines），按 id 收逐条回执。

    引擎落位成功回 "shown"、无空余 lane 回 "skipped"。render 在上界
    （≤RECEIPT_TIMEOUT）内等回执：shown→"shown"（确认真渲染）；skipped/超时/旧引擎
    无回执→"sent"（已投递但未确认渲染）；写失败→"failed"。等待有上界，不拖累守护
    进程 1s 主循环。点击穿透。
    """

    RECEIPT_TIMEOUT = 0.5

    def __init__(self, spawn=None) -> None:
        self.proc: subprocess.Popen | None = None
        self._reader: ReceiptReader | None = None
        self._spawn = spawn or _spawn_engine
        self._next_id = 0
        self.stats = {"shown": 0, "push_fail": 0, "skipped": 0, "unconfirmed": 0}

    def _ensure(self) -> bool:
        if self.proc is not None and self.proc.poll() is None:
            return True
        try:
            self.proc = self._spawn()
        except OSError:
            return False
        self._reader = ReceiptReader(self.proc.stdout)
        self._reader.start()
        return True

    def render(self, content: str, mood: str, salience: float) -> str:
        """出口自行做介质翻译（mood→色、salience→字号）。返回真实 status 回执。"""
        if not self._ensure():
            return "failed"
        self._next_id += 1
        mid = self._next_id
        try:
            self.proc.stdin.write(json.dumps(
                {"id": mid, "text": content,
                 "color": MOOD_COLORS.get(mood, "#FFFFFF"),
                 "size": "large" if salience >= LARGE_SALIENCE else "small"},
                ensure_ascii=False) + "\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError):
            self.proc = None
            self._reader = None
            self.stats["push_fail"] += 1
            return "failed"
        receipt = self._reader.wait(mid, self.RECEIPT_TIMEOUT) if self._reader else None
        if receipt == "shown":
            self.stats["shown"] += 1
            return "shown"
        if receipt == "skipped":
            self.stats["skipped"] += 1
        else:
            self.stats["unconfirmed"] += 1
        return "sent"

    def close(self) -> None:
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.stdin.close()
            except OSError:
                pass


class Persona:
    """「弹幕君」人格插件实例（N 人格 = N 实例）。"""

    def __init__(self, pid: str, store, model: ModelClient, trajectory: Trajectory,
                 cfg, outlet: DanmakuOutlet | None = None, log=print) -> None:
        self.pid = pid
        self.store = store
        self.model = model
        self.trajectory = trajectory
        self.cfg = cfg
        self.outlet = outlet or DanmakuOutlet()
        self.log = log
        self.mode = getattr(cfg, "persona_mode", "assistant")
        self.stats = {"drafts": 0, "throttled": 0, "dup": 0, "silence": 0,
                      "parse_fail": 0, "spoken": 0}
        self._system = (PERSONA_SYSTEM_ASSISTANT if self.mode == "assistant"
                        else PERSONA_SYSTEM_COMPANION)
        from .insight import InsightMiner
        self.miner = InsightMiner(store, cfg)
        self._insights: list[dict] = []

    # —— 触发判定（封闭集合，DESIGN §4 链路 G）——
    def should_speak(self, mini_row) -> tuple[bool, str]:
        self._insights = self.miner.candidates() if self.mode == "assistant" else []
        if self._insights:
            return True, f"洞察：{self._insights[0]['detail'][:24]}"
        imp = (json.loads(mini_row["payload"] or "{}").get("importance")) \
            if mini_row["payload"] else None
        if imp is not None and imp >= 0.7:
            return True, f"importance={imp}"
        if self.store.conn.execute(
                "SELECT count(*) c FROM kb WHERE open=1").fetchone()["c"]:
            # KB 命中在见证 context 组装时发生；这里近似：近期事实包含 kb key
            hits = self.kb_hit_texts(mini_row["content"] or "", mark=False)
            if hits:
                return True, "KB命中"
        return False, ""

    def kb_hit_texts(self, text: str, mark: bool = True) -> list[str]:
        from .kb import KB
        return KB(self.store).hits_for(text, mark=mark)

    # —— 主入口：一条新事实 → 闸门 → 表达 ——
    def on_fact(self, mini_row) -> int | None:
        fire, reason = self.should_speak(mini_row)
        if not fire:
            return None
        messages = self._context(mini_row, reason)
        draft = None
        for _ in (1, 2):
            text, usage = self.model.chat(messages)
            self.stats["drafts"] += 1
            self.trajectory.record(f"persona:{self.pid}", self.model.model,
                                   messages, text, usage)
            self.store.insert("metrics", ts_wall=Stamp.now().wall, ts_mono=Stamp.now().mono,
                              purpose=f"persona:{self.pid}", model=self.model.model,
                              tokens_in=usage.get("in", 0), tokens_out=usage.get("out", 0),
                              cached_tokens=usage.get("cached", 0),
                              latency_ms=usage.get("latency_ms", 0))
            draft = parse_draft(text)
            if draft is not None:
                break
            self.stats["parse_fail"] += 1
        if draft is None:
            return None  # 人格调用失败→本轮沉默（链路 G）
        if draft.get("silence"):
            self.stats["silence"] += 1
            self.log(f"[persona:{self.pid}] 沉默权（{reason}）")
            return None

        # —— 引擎闸门（施加于草稿之后）——
        now = time.time()
        last = self.store.conn.execute(
            "SELECT ts_wall FROM expressions WHERE kind='speak' ORDER BY ts_wall DESC "
            "LIMIT 1").fetchone()
        if last and now - last["ts_wall"] < self.cfg.min_roast_interval:
            self.stats["throttled"] += 1
            return None
        if self._dup(draft["content"]):
            self.stats["dup"] += 1
            return None

        xid = self.store.insert(
            "expressions", ts_wall=now, kind="speak", content=draft["content"],
            channel="danmaku", mood=draft["mood"], salience=draft["salience"],
            dedup_key=draft["content"][:12],
            source_refs=[{"table": "messages", "id": mini_row["id"]}],
            status="sent")
        status = self.outlet.render(draft["content"], draft["mood"], draft["salience"])
        self.store.conn.execute("UPDATE expressions SET status=? WHERE id=?",
                                (status, xid))
        self.store.conn.commit()
        self.stats["spoken"] += 1
        self.log(f"[persona:{self.pid}] 弹幕#{xid} {status} 「{draft['content']}」"
                 f"（{draft['mood']}/{draft['salience']:.1f}←{reason}）")
        return xid

    def _dup(self, content: str) -> bool:
        """与近 5 条发言重叠 ≥60% 视为重复（字符 bigram Jaccard 简化：重叠率）。"""
        recent = [r["content"] for r in self.store.conn.execute(
            "SELECT content FROM expressions WHERE kind='speak' "
            "ORDER BY ts_wall DESC LIMIT 5")]
        grams = lambda s: {s[i:i + 2] for i in range(len(s) - 1)} if len(s) > 1 else {s}
        g = grams(content)
        for r in recent:
            gr = grams(r)
            if g and gr and len(g & gr) / max(1, len(g | gr)) >= 0.6:
                return True
        return False

    # —— 周期 digest（WALKTHROUGH T8）——
    def on_digest(self) -> int | None:
        if self.mode == "assistant" and not self.miner.candidates():
            self.stats["silence"] += 1
            return None  # 无洞察则定期也不开口（不说话 > 说废话）
        recent = self.store.session_stream(limit=10)
        if not recent:
            return None
        body = "\n".join(f"{fmt_wall(r['ts_start'])} {r['content']}" for r in recent[:5])
        messages = self._digest_context(body)
        for _ in (1, 2):
            text, usage = self.model.chat(messages)
            self.stats["drafts"] += 1
            self.trajectory.record(f"persona:{self.pid}:digest", self.model.model,
                                   messages, text, usage)
            draft = parse_draft(text)
            if draft is not None:
                break
            self.stats["parse_fail"] += 1
        if draft is None or draft.get("silence"):
            self.stats["silence"] += 1
            return None
        now = time.time()
        last = self.store.conn.execute(
            "SELECT ts_wall FROM expressions WHERE kind='speak' ORDER BY ts_wall DESC "
            "LIMIT 1").fetchone()
        if last and now - last["ts_wall"] < self.cfg.min_roast_interval:
            self.stats["throttled"] += 1
            return None
        if self._dup(draft["content"]):
            self.stats["dup"] += 1
            return None
        xid = self.store.insert(
            "expressions", ts_wall=now, kind="speak", content=draft["content"],
            channel="danmaku", mood=draft["mood"], salience=draft["salience"],
            dedup_key=draft["content"][:12], source_refs=[], status="sent")
        status = self.outlet.render(draft["content"], draft["mood"], draft["salience"])
        self.store.conn.execute("UPDATE expressions SET status=? WHERE id=?", (status, xid))
        self.store.conn.commit()
        self.stats["spoken"] += 1
        self.log(f"[persona:{self.pid}] digest 弹幕#{xid} {status} 「{draft['content']}」")
        return xid

    def _digest_context(self, body: str) -> list[dict]:
        st = self.store
        ctx = self._system + "\n"
        ctx += f"[USER_MODEL]\n{json.dumps(st.get_state('user_model', {}), ensure_ascii=False)}\n"
        if self.mode == "assistant":
            ins = self.miner.candidates()
            if ins:
                ctx += "[洞察候选]\n" + "\n".join(i["detail"] for i in ins) + "\n"
            else:
                ctx += "[洞察候选]\n（无）——silence 是正确输出\n"
        ctx += f"[近期事实]\n{body}\n"
        tail = st.conn.execute(
            "SELECT content FROM expressions WHERE kind='speak' "
            "ORDER BY ts_wall DESC LIMIT 5").fetchall()
        if tail:
            ctx += "[expressions尾部]\n" + "\n".join(r["content"] for r in tail) + "\n"
        turn = json.dumps({"trigger": "digest", "now": fmt_wall(time.time())},
                          ensure_ascii=False)
        return [{"role": "system", "content": ctx},
                {"role": "user", "content": turn}]

    def _context(self, mini_row, reason: str) -> list[dict]:
        st = self.store
        ctx = self._system + "\n"
        ctx += f"[USER_MODEL]\n{json.dumps(st.get_state('user_model', {}), ensure_ascii=False)}\n"
        if self.mode == "assistant":
            if self._insights:
                ctx += "[洞察候选]\n" + "\n".join(i["detail"] for i in self._insights) + "\n"
            else:
                ctx += "[洞察候选]\n（无）——若新事实本身不构成用户不知道的信息，请沉默\n"
        ctx += f"[新事实]\n{fmt_wall(mini_row['ts_start'])} {mini_row['content']}\n"
        kb_hits = self.kb_hit_texts(mini_row["content"] or "")
        if kb_hits:
            ctx += "[KB命中行]\n" + "\n".join(kb_hits) + "\n"
        tail = st.conn.execute(
            "SELECT content FROM expressions WHERE kind='speak' "
            "ORDER BY ts_wall DESC LIMIT 5").fetchall()
        if tail:
            ctx += "[expressions尾部]\n" + "\n".join(r["content"] for r in tail) + "\n"
        turn = json.dumps({"trigger": reason, "now": fmt_wall(time.time())},
                          ensure_ascii=False)
        return [{"role": "system", "content": ctx},
                {"role": "user", "content": turn}]
