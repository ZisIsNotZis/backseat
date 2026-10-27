<p align="center"><img src="assets/logo.svg" alt="backseat" width="196" height="56"></p>

**English** | [简体中文](README.zh-CN.md)

# backseat

### It rides in the back seat of your life, watches the whole trip, and can't help commenting — it takes the wheel only when you nod.

**Backseat is a local-first AI companion that compresses what happens on your screen into layered long-term memory of you — moments → episodes → behaviour → intent → portrait — and only speaks when it knows something you don't.**

The usual path for an AI product is to mine a pain point, send a forward-deployed engineer in, customise, deliver — expensive, slow, and only affordable for the sharpest pain. Backseat takes the opposite path: deploy first, let it run, and let the understanding grow out of the data on its own.

Everything up to the text leaving your machine is local: frames are sampled, de-duplicated and hashed on your own box, and the model calls go to your own OpenAI-compatible endpoint. The danmaku overlay is just its first mouth, not the product.

## Quick start

Requires Linux with an X11 session, Python 3.12+, `ffmpeg`, `x11-utils` (`xprop`/`xdpyinfo`), `python3-pyqt5`.

```sh
git clone https://github.com/ZisIsNotZis/backseat.git && cd backseat
./scripts/install.sh                      # checks system deps, builds venv, writes a systemd user service
systemctl --user set-environment LITELLM_BASE_URL=http://127.0.0.1:4000
systemctl --user set-environment LITELLM_API_KEY=sk-...
systemctl --user enable --now backseat     # or: python3 -m backseat --data-dir ~/backseat-data
python3 -m backseat --stats                # calls, tokens, cache hit rate, frames, message levels
```

Without `LITELLM_BASE_URL`/`LITELLM_API_KEY` the daemon still runs in L0-only mode: it captures frames and writes observations, and says so on start-up. All tunables live in `config.example.toml` — copy it to `config.toml` (gitignored) to override. They are derived from three flexible budgets (`storage_bytes`, `daily_requests`, `effort`), not magic constants.

```sh
cp config.example.toml config.toml   # then edit
./scripts/dev-up.sh                  # synthetic frame source: Xvfb :99 + mpv loop, daemon → ~/backseat-data/run.log
./scripts/dev-up.sh --real           # or capture the real desktop ($DISPLAY)
./scripts/dev-down.sh                # stop daemon + video source (never touches your LiteLLM proxy)
```

## What it can do now

- **Screen sensor (L0)** — X11 frame sampling with a cadence controller (low/mid/high tiers driven by pHash dynamics EMA), pHash gating, blank-frame rejection, a ~120 s RAM ring so capture rate ≠ processing rate, closed-enum event detection, JPEG frames on disk with hash/key/bytes recorded.
- **Witness layer (L1)** — pixels → text, the only conversion point. anchor/diff modes with forced re-anchor, strict JSON output contract, retry-then-drop on parse failure, per-call token/cost/cache/latency metering plus a full `trajectory/` JSONL.
- **Evidence basis, not vibes** — every fact row carries the observation ids it can be checked against; comparison sentences ("switched from X to Y") pull the base frame into that row's citations; a nickname may only be used if it is re-named to a frame in the same row, otherwise **the whole row is refused** rather than silently carrying a stale name.
- **Memory pyramid (L2)** — small → medium → big merging (K/M thresholds), incremental `USER_MODEL` merge, restart recovery from bigs + mids + anchor.
- **Recall (KB + flashback)** — keys are both retrieval and trigger words, `!always` periodic replay, `!at=YYYY-MM-DD HH:MM` timed reminders, 60-minute de-duplication, flashback with verbatim replay plus frame references that degrade explicitly (never an empty answer without saying why).
- **Insight miner (no LLM)** — deterministic detection of repetition, interval, accumulation and anomaly across time; the product rule is that the only reason to speak is information asymmetry.
- **Persona + danmaku outlet (M5)** — the "弹幕君" persona is a plugin; the engine applies its gates *after* drafting (throttle, ≥60 % overlap dedupe, right to silence); the PyQt5 click-through overlay runs as a subprocess over stdin JSON-lines and acknowledges every message, so "shown" means rendered rather than merely sent.
- **Storage budget and eviction** — `storage_bytes` derives retention; when over budget the oldest non-exempt frames are evicted FIFO and the water line is logged.
- **Measurement** — `metrics` table, `--stats` summary, `--once N` smoke runs, a read-only eval ruler over frozen snapshots, a defect census with meaningful exit codes (0 clean / 1 defects / 2 population empty), and a write rehearsal that re-writes historical text under current rules without calling the model.

## Architecture

```
backseat/            the engine
  sensors/           x11 probe, screen sensor (sampling, ring, selector), pHash
  witness.py         L1: frames → facts (evidence basis + write rules live here, nowhere else)
  compressor.py      L2: small→medium→big merging
  kb.py              recall, scheduled replay, flashback
  insight.py         deterministic cross-time observation
  persona.py         the persona plugin and its output gates
  outlets/           danmaku_engine: PyQt5 overlay, JSON-lines in, receipts out
  store.py           SQLite: observations / messages / kb / expressions / metrics / state
  bus.py clock.py config.py model.py trajectory.py
evals/               read-only ruler over a frozen snapshot (no model, no network)
scripts/             install, dev-up/down, freeze_snapshot, d1_census, rehearse_writes, backfill_epithets, atspi_probe
tests/               14 standalone test files, each runnable on its own
docs/                numbered 0–9 design record (problem → goal → constraints → use cases → product → architecture → interface → detail → implementation)
```

Two orthogonal axes carry the whole model: **time** (moment / short / mid / long) × **semantics** (raw / appearance / behaviour / portrait). Sensors and outlets are channels, so they are field values (`sensor`, `channel`) rather than tables; the core engine depends on no particular channel. Facts belong to the engine and style belongs to the persona: the witness and compressor produce nothing but facts, and any "how to say it" lives in a persona plugin. Because the table schema describes the engine itself, changing it means a migration.

Start reading at [`docs/6_ARCHITECTURE.md`](docs/6_ARCHITECTURE.md) (subsystem boundaries and the decision ledger) and [`docs/7_INTERFACE.md`](docs/7_INTERFACE.md) (table and API contracts).

## How to develop

```sh
python3 -m backseat --once 5 --data-dir /tmp/bs-smoke   # smoke run, no model needed
python3 -m backseat --stats --data-dir /tmp/bs-smoke     # metering summary

# each test file is standalone; ALL PASS (N) at the end means green
for f in tests/test_*.py; do python3 "$f" >/dev/null || echo "$f FAIL"; done

python3 scripts/freeze_snapshot.py --verify              # hash a frozen snapshot, exit 1 on mismatch
python3 scripts/d1_census.py --db ~/backseat-data/snapshots/<UTC-date>/backseat.db
```

Operational detail — endpoint checks, eviction water lines, replay recomputation, known pitfalls, rollback — lives in [`docs/9_IMPLEMENTATION.md`](docs/9_IMPLEMENTATION.md). Design changes go to the docs layer (`docs/6_ARCHITECTURE.md` for decisions, `docs/7_INTERFACE.md` for contracts, `docs/8_DETAIL.md` for write rules) **before** the behaviour changes.

## Contributing

Read [`CONTRIBUTING.md`](CONTRIBUTING.md) first — it is four sentences of house rule. In short: one commit per concern with its measured evidence, facts stay in the engine and style stays in the persona, anything a deterministic algorithm can decide the model must not touch, and ported code keeps its origin and license.

## Roadmap

**Implemented (Release 2.0, code complete)**

- M0 skeleton and data layer — event bus (wall + monotonic timestamps), six-table SQLite schema, all parameters derived from flexible budgets.
- M1 screen sensor — capture, pHash gating, blank rejection, event detection, cadence controller.
- M2 witness layer — anchor/diff, strict JSON, metering, trajectory.
- M3 memory pyramid — small/medium/big merge, `USER_MODEL`, restart recovery.
- M4 recall — KB injection, `!always`/`!at`, flashback with degradation.
- M5 persona + danmaku outlet — engine gates after drafting, receipts.
- M6 packaging — install script, systemd unit, LICENSE, this README.

Some confirmation gates still need real usage rather than more code: a day of real capture, a callback demo on a real desktop, and a human score on danmaku frequency and tone. The perception layer has reached the limit of what can be verified without that data, so it moves to maintenance and the next target is elsewhere.

**Planned**

- Release 2.1 — pluggable input streams (any frame source: RTSP, image directory, another display, video file, not just `config.display`), a resident AT-SPI sensor with opportunistic D-Bus, a bounded deep-dive/investigation layer, pinned frames that survive eviction, local VLM options.
- Release 2.2 — a second mouth: system notifications, a CLI outlet (`backseat ask "what did I do this week"`), and portrait export as JSON/Markdown.
- Release 3.x — the help layer: from describing to proposing with evidence, then acting inside a bounded, dry-run-first action surface. The long-term target is a cluster of such companions whose combined understanding beats any single person's partial view — which is why the data model has been kept clean from M0 on.

Milestones, gates, thresholds and current status live in [`docs/0_ROADMAP.md`](docs/0_ROADMAP.md); the problem it refuses to solve lives in [`docs/1_PROBLEM.md`](docs/1_PROBLEM.md).

## License

AGPL-3.0. Copyright (C) ZisIsNotZis. The danmaku engine is ported from the same author's `danmaku` project — same origin, same license.
