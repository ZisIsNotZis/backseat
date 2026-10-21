kind:    report
from:    orient-backseat (package launched by root)
to:      root
spend:   idle-compute
id:      none — first contract, answered in the run final message

First orientation report, filed here by root. Body is the run report verbatim.

Implemented: orientation of `backseat` — no writes; the deliverable is the report below.

---

# Report → root (/home/z/vibe)

**node whose roadmap this changes:** root (candidate next milestone for `backseat`)
**evidence pointer:** `/home/z/vibe/backseat` @ `299c11c`, frozen DB snapshot `~/backseat-data/snapshots/2026-10-19/backseat.db` (`MANIFEST.json`, `git_rev 63ff9e3`)
**decision needed:** which side of the M0′ gate moves — code or criterion (Q1); and the scope of M0′ (Q2). Q3–Q5 are smaller and listed after.

## (a) What the project is

`backseat` is a local-first, zero-config AI long-term companion for Linux/X11 (`pyproject.toml` v2.0.0a1, AGPL-3.0). It listens rather than converses: a screen sensor feeds an L0 fact set, an LLM witness turns frames into strictly-JSON one-line facts (`small` messages), a pyramid folds them into `medium`/`big`, a persona layer drafts expressions, and outlet plugins (danmaku first) render them. All cognition goes to a user-supplied OpenAI-compatible endpoint (`http://127.0.0.1:4000`, not managed by the project); data lives in `~/backseat-data/`.

Shape: Python 3.12, stdlib-only core + PyQt5 for the danmaku outlet, 6 SQLite tables (`observations/messages/kb/expressions/metrics/state`) plus a `session_stream` view, 18 modules (~2.1k lines), 11 stdlib test files (~2.3k lines), an eval harness `evals/` (~870 lines), scripts for install/dev/freeze. Documents: `docs/{DESIGN,SCHEMA,RUNBOOK,WALKTHROUGH,ROADMAP,PAIN}.md`, tickets in `.scratch/tickets/`.

Where it stands: Release 2.0 (perception + danmaku) is code-complete M0–M6 with green tests at HEAD; its confirmation gates are only partly closed (M1 needs a real-day run; M4's callback demo not done — `kb` has **0 rows** on real data; M5 needs a user score; M6 needs a fresh-machine install and a GitHub tag, and there are no tags). The roadmap has already declared Release 2.0 at its capability ceiling and made Release 3.0 "witness" the mainline, whose first milestone is **M0′ data base and evaluability** (`docs/ROADMAP.md:102-125`). M0′ is partly delivered: semantic-state schema, eval ruler with three baselines, big-layer back-reference fix, frozen snapshot. Missing, by its own text: "尚未交付五通道接入器中除 A 以外的四条，以及各通道的退出判据."

Live state: the daemon is **not running** (last log tick `[10-19 06:44:04] tick=62760`), the model endpoint was refusing connections (`~/backseat-data/run.log`), `state.witness_cursor = 42395` vs `observations = 42396` (backlog 1), `observations` 42,396 with exactly one sensor (`screen`), messages 24,551 small / 1,227 medium / 122 big, expressions 366 all `shown`. Working tree clean; all 11 tests pass at HEAD.

## (b) Milestone it proposes next

**Close M0′ — do not open M1′ yet.**

Evidence pointer: `docs/ROADMAP.md:113-125` (gate = D1 pass ∧ cursor backlog 0 ∧ replay determinism 100%; status lines admit four adapters and all per-channel exit criteria are not delivered) against measurements on the frozen snapshot:

| gate item | measured | verdict |
|---|---|---|
| D1 A (semantic back-reference) | hand-run on the D1 procedure: 200 `small` rows sampled id-ordered from offset 500 → **163/200 records pass**, 1,103 refs of which **94 land outside `[ts_start, ts_end]`**; whole-snapshot: 104/24,551 messages and 232/27,012 refs affected | **not met** |
| D1 B (independent review of 50) | no runner, no recorded result — `grep D1` returns only docs | **not run** |
| cursor backlog 0 | 1 (pipeline stopped 2 days ago) | **not met** |
| replay determinism 100% | `test_freeze.py` green; two runs of `evals/run_eval.py --n 50` on the same snapshot identical | **met** |

Cause of the D1 A failure is narrow and local: `Witness.process_pending` fetches observations `ORDER BY id` (`backseat/witness.py:242-244`) while frames are inserted with `ts_wall` not monotone in `id`, and `process_batch` takes `ts_start=rows[0]`/`ts_end=rows[-1]` (`backseat/witness.py:228`). Members therefore fall outside the window by seconds, and 12 of 24,551 rows end up with an **inverted** window (`ts_start > ts_end`) — none in `medium`/`big`. The refs themselves are fine: `ref_id_missing = 0`, and every `small` row carries non-empty `obs_ids` (24,551/24,551).

Proposed packages for M0′ closure (root's call on ordering and identity):

1. **D1 judge as code** — sample 200 `small` rows by a stated seed/method, emit A per record and per ref, write the numbers into ROADMAP; run B's 50-row review with a named independent actor and land the verdict. Turns a hand-run procedure into a rerunnable one.
2. **Batch-window repair** — `ts_start=min`, `ts_end=max` over batch members, plus a test asserting every `obs_ids` member of a new row lies inside its own window; decide forward-only vs backfill for the 104 historic rows.
3. **Clear the cursor backlog** — needs the user's LiteLLM proxy (resource, see Q4).
4. **Channels B/C/E adapters + per-channel exit criteria** — or narrow M0′ to channel A and re-home B/C/E (Q2). Today `EventKind` (`backseat/bus.py`) has screen kinds only; `atspi/fs/git/proc/input/audio` are explicitly marked "未实现" in `docs/SCHEMA.md`.
5. Optional, unblocked: window title as a queryable column (`evals/questions.py:UNAVAILABLE_FIELDS` already names its absence as the reason no title-sequence questions exist).

M1′ cannot start honestly before this: its deliverable is a per-channel A/B/C/E matrix and three of those channels have no input, and its baselines need the title column it lists.

## (c) Questions this package cannot answer

**Q1 — `contract` (M0′ gate).** D1 A is unreachable as written; which side moves? *Tried:* ran the D1 procedure by hand on the frozen snapshot (numbers above), read `witness.py`/`store.py` to the cause, confirmed refs themselves resolve. *Closed paths:* tolerating it silently is not available — the gate is a全称 judgment in the roadmap; declaring a pass without B is not observable. *What would reopen:* a ruling that (i) `witness` sets the window from min/max of members and the 104 historic rows are either accepted as known-degraded or re-derived, or (ii) D1 A is restated with a tolerance / per-ref denominator / explicit treatment of inverted windows. *Cost:* (i) ~10 lines + one test; (ii) a SCHEMA edit and a rerun. Either changes a gate the root owns.

**Q2 — `direction` (scope).** Is M0′ all five channels, or channel A plus a narrowed M0′ and B/C/E as their own milestone? *Tried:* measured that only channel A has data (single `screen` sensor, `kb` 0 rows) and that `evals/` can now score A end-to-end (50/50 `check_sql` recompute, D2 citation 100/100, B1 38.0% overall / 18.4% semantic subset); checked `bus.py` and `SCHEMA.md` for the other four sensors — absent. *Closed paths:* delivering five adapters in one package would be a multi-day build whose acceptance is a per-channel exit criterion nobody has written yet. *What would reopen:* the user saying whether "五条通道接入器" is the price of entering M1′, or whether A-alone evidence is enough to start M1′'s harness work.

**Q3 — `contract` (labels).** `docs/SCHEMA.md` declares `labels` as a 7th table and channel B's judge depends on it; the M0 gate froze six tables and says changes need migration. *Tried:* read `store.py`'s schema (6 tables + 1 view, no `labels`), confirmed nothing implements it, and confirmed M0′'s gate text does not list it. *Closed paths:* proceeding would contradict the frozen-schema gate. *What would reopen:* explicit authorization of the migration and of `labels` as in-scope for M0′.

**Q4 — `resource`.** Gate item "cursor backlog 0" needs a running pipeline, i.e. the user's proxy at `127.0.0.1:4000`; it was refusing connections and the daemon has been down since 2026-10-19. *Tried:* `pgrep`, `run.log` tail, `state.witness_cursor` vs `count(observations)`. *Closed paths:* idle compute cannot substitute for a metered external endpoint. *What would reopen:* the user starting the proxy, or naming an alternate endpoint/model.

**Q5 — `contract` (D1 B).** "由独立评审对 50 条抽样判定" names no actor, no interface and no place to record the verdict; the repo has no D1 tooling (`evals/` implements D2 only). *Tried:* grep for D1 across code and docs. *Closed paths:* a self-review would defeat the word "独立". *What would reopen:* the root naming the reviewer and the artifact that holds the result.

---

Implemented: orientation only — nothing written to the project.
Changed files: none (`git status --short` empty).
Validation: 11/11 test files pass at HEAD; `evals/run_eval.py --n 50` on the frozen snapshot exits 0 with 50/50 `check_sql`, 100/100 D2 citations; D1 A hand-run on the same snapshot yields 163/200 records; snapshot/eval reads were `mode=ro`.
Open risks/questions: Q1–Q5 above; the snapshot is 2026-10-19 and the pipeline is stopped, so all "current" counts are two days old.
Recommended next step: root rules on Q1/Q2, then dispatch M0′-closure packages 1–2 (idle compute, no user action) while Q4 waits on the user.