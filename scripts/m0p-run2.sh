#!/usr/bin/env sh
# M0′ 第二轮实跑：产品运行时模型（config.toml 的 model）+ 合成帧源，跑到 ≥200 行 small 或 6 小时上限。
#
# 一次调用就够，不需要人守：本脚本自己预检、回填、起守护进程、盯行数、让路、停、冻结快照、普查，
# 最后写 ~/backseat-data/run2.DONE（存在即结束，内容是行数/普查退出码/花销/原因）。
# 过程读数：~/backseat-data/run2.driver.log（每 tick 一行：行数、已耗时、load1）。
# 让路：touch /tmp/backseat-run2.yield，或 flashdb 的 measure/bench 类进程出现 → 停在本轮最后一次写入，
#       等它消失再续（flashdb 的静默窗做计时读数，读错比晚一批判定人口更贵）。
set -u
cd "$(dirname "$0")/.."

DATA="${HOME}/backseat-data"
BASE_MSG_ID=25908          # 第一轮（deepseek）最后一行；本轮判定人口从它之后开始（SCHEMA 模型分界）
TARGET_ROWS=200
MAX_SECONDS=21600          # 6 小时 idle 上限
STALL_SECONDS=900          # 15 分钟零落库就判失败（不空转到 6 小时）
TICK=30
LOG="$DATA/run2.driver.log"
DONE="$DATA/run2.DONE"
SENTINEL="/tmp/backseat-run2.yield"
SNAP="$DATA/snapshots/$(date -u +%Y-%m-%d)-run2"
MODEL=$(sed -n 's/^model *= *"\(.*\)"/\1/p' config.toml | head -1)
PRICE_IN="0.10"            # 网关价表 $/M in（curl /model/info）；实际扣的是 Copilot 月配额
PRICE_OUT="0.50"           # $/M out

mkdir -p "$DATA"
rm -f "$DONE"
say() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >> "$LOG"; }
rows() { sqlite3 "$DATA/backseat.db" \
  "SELECT count(*) FROM messages WHERE id > $BASE_MSG_ID AND level='small';" 2>/dev/null || echo 0; }
daemon_alive() { pgrep -f "python3 -m backseat --data-dir" >/dev/null 2>&1; }
flashdb_busy() { pgrep -af 'flashdb' 2>/dev/null | grep -Eqi 'measure|bench|fio|drift'; }
money() {
  python3 - "$DATA" "$START_EPOCH" "$START_FILE" "$START_LINES" "$MODEL" "$PRICE_IN" "$PRICE_OUT" <<'PY'
import glob, json, os, sys
data, start_epoch, start_file, start_lines, model = sys.argv[1], int(sys.argv[2]), sys.argv[3], int(sys.argv[4]), sys.argv[5]
pin, pout = float(sys.argv[6]), float(sys.argv[7])
calls = tin = tout = tcached = 0
for f in sorted(glob.glob(os.path.join(data, 'trajectory', '*.jsonl'))):
    try:
        if os.path.getmtime(f) < start_epoch - 120:
            continue
    except OSError:
        continue
    with open(f, encoding='utf-8', errors='replace') as fh:
        for i, line in enumerate(fh):
            if os.path.abspath(f) == os.path.abspath(start_file) and i < start_lines:
                continue
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get('model') and d['model'] != model:
                continue
            u = d.get('usage') or {}
            calls += 1
            tin += int(u.get('in') or 0); tout += int(u.get('out') or 0); tcached += int(u.get('cached') or 0)
if calls == 0:
    print('calls=0 tokens=unknown cost=unknown（未取得该模型的调用，按缺席记录，不记 0）')
else:
    cost = tin / 1e6 * pin + tout / 1e6 * pout
    print(f'calls={calls} in={tin}（cached={tcached}） out={tout} '
          f'cost=${cost:.4f}（按网关价表 ${pin}/M in、${pout}/M out）')
PY
}
finish() {  # $1 = 结束原因
  ./scripts/dev-down.sh >> "$LOG" 2>&1 || true
  ROWS=$(rows); ELAPSED=$(( $(date +%s) - START_EPOCH ))
  say "结束（$1）：落库 $ROWS 行、耗时 ${ELAPSED}s、模型 $MODEL"
  FREEZE=skipped; CENSUS_EXIT=not-run
  if [ "$ROWS" -gt 0 ] 2>/dev/null; then
    if python3 scripts/freeze_snapshot.py --dir "$SNAP" >> "$LOG" 2>&1; then
      FREEZE="$SNAP"
      python3 scripts/d1_census.py --db "$SNAP/backseat.db" --since-msg-id "$BASE_MSG_ID" \
        --json "$DATA/run2.census.json" >> "$LOG" 2>&1
      CENSUS_EXIT=$?
      say "普查（--since-msg-id $BASE_MSG_ID）退出 $CENSUS_EXIT；明细 $DATA/run2.census.json"
    else
      FREEZE="failed（$SNAP）"
    fi
  else
    say "零行：不冻结快照（冻结一个没有新行的库只会让人以为跑过了）"
  fi
  COST=$(money); say "花销 $COST"
  {
    echo "reason=$1"
    echo "model=$MODEL"
    echo "started=$(date -u -d @"$START_EPOCH" +%Y-%m-%dT%H:%M:%SZ)"
    echo "elapsed_seconds=$ELAPSED"
    echo "rows_small_after_$BASE_MSG_ID=$ROWS"
    echo "target_rows=$TARGET_ROWS"
    echo "snapshot=$FREEZE"
    echo "census_exit=$CENSUS_EXIT"
    echo "census_json=$DATA/run2.census.json"
    echo "cost=$COST"
    echo "log=$LOG"
  } > "$DONE"
  say "DONE 已写：$DONE"
  exit 0
}

say "== 第二轮实跑开始：model=$MODEL base_msg_id=$BASE_MSG_ID target=$TARGET_ROWS max=${MAX_SECONDS}s =="

# 1) 预检：网关可达 + 该模型在路由表里（不可达就当场判失败，不空转 6 小时）
if ! curl -sf -m 15 -H "Authorization: Bearer ${LITELLM_API_KEY:-}" \
      http://127.0.0.1:4000/v1/models -o /tmp/run2.models.json; then
  say "预检失败：网关 127.0.0.1:4000 /v1/models 不可达"
  finish "gateway-unreachable"
fi
if ! python3 -c "import json,sys;ids={m['id'] for m in json.load(open('/tmp/run2.models.json'))['data']};sys.exit(0 if '$MODEL' in ids else 1)"; then
  say "预检失败：路由表里没有 $MODEL"
  finish "model-not-routed"
fi
say "预检通过：网关 200、$MODEL 在路由表里"

# 2) 称呼册先拿历史回填（幂等；C2 的沿用跨全库比，不回填会把历史名字当首次出现放行）
python3 scripts/backfill_epithets.py --db "$DATA/backseat.db" >> "$LOG" 2>&1 || say "警告：回填失败，继续（C2 的读数会偏松）"

# 3) 记录起点（花销按 trajectory 的 ts 之后算，不按文件日期）
START_EPOCH=$(date +%s)
TODAY=$(date -u +%Y-%m-%d)
START_FILE="$DATA/trajectory/$TODAY.jsonl"
START_LINES=$( [ -f "$START_FILE" ] && wc -l < "$START_FILE" || echo 0 )
START_LINES=${START_LINES:-0}
say "起点：epoch=$START_EPOCH trajectory=$START_FILE 已有 $START_LINES 行、库里已有 $(rows) 行新 small"

# 4) 起环境（Xvfb + mpv + 守护进程；config.toml 就是产品配置）
./scripts/dev-up.sh >> "$LOG" 2>&1 || { say "dev-up 失败"; finish "dev-up-failed"; }
sleep 20
if ! daemon_alive; then say "守护进程没起来（见 run.log 尾部）"; finish "daemon-died-at-start"; fi

# 5) 盯行数
LAST_ROWS=0; LAST_PROGRESS=$START_EPOCH; PAUSED=0
while :; do
  ROWS=$(rows); NOW=$(date +%s); ELAPSED=$(( NOW - START_EPOCH ))
  LOAD1=$(cut -d' ' -f1 /proc/loadavg)
  say "rows=$ROWS elapsed=${ELAPSED}s load1=$LOAD1 paused=$PAUSED"
  [ "$ROWS" != "$LAST_ROWS" ] && { LAST_ROWS=$ROWS; LAST_PROGRESS=$NOW; }
  [ "$ROWS" -ge "$TARGET_ROWS" ] 2>/dev/null && finish "target-reached"
  [ "$ELAPSED" -ge "$MAX_SECONDS" ] && finish "time-cap"
  if [ $(( NOW - LAST_PROGRESS )) -ge "$STALL_SECONDS" ]; then
    if daemon_alive; then say "守护进程在跑但 15 分钟零落库（见 run.log 尾部：整行拒收/错误）"; finish "stalled"
    else say "守护进程已死"; finish "daemon-died"; fi
  fi
  if [ "$PAUSED" -eq 0 ] && { [ -f "$SENTINEL" ] || flashdb_busy; }; then
    say "让路：停在本轮最后一次写入（哨兵=$([ -f "$SENTINEL" ] && echo yes || echo no) flashdb=$([ flashdb_busy ] && echo busy || echo idle)）"
    ./scripts/dev-down.sh >> "$LOG" 2>&1 || true
    PAUSED=1
  fi
  if [ "$PAUSED" -eq 1 ] && [ ! -f "$SENTINEL" ] && ! flashdb_busy; then
    say "静默窗结束：续跑"
    ./scripts/dev-up.sh >> "$LOG" 2>&1 || true
    PAUSED=0; LAST_PROGRESS=$(date +%s)
  fi
  sleep "$TICK"
done
