"""预演冒烟：`scripts/rehearse_writes.py` 与 `process/process_batch` 是同一条写入路径。

覆盖三件事（都不是门槛，门槛住 ROADMAP，由项目自己的节点判）：
a) 落库形状：`process` 与 `process_batch` 经 `_write_small` 落下的字段与抽取前一致——
   逐字段断言，不靠"提交信息说它没变"（这张断言同时保护 `_write_small` 的抽取）；
b) 预演不是第二套规则：同一批内容，`rehearse` 写出的行与 `process`/`process_batch`
   写出的行**逐字段相同**；且预演一个模型请求都不发（`NoModel.chat` 会抛）；
c) 坏输入单独计数：`obs_ids` 为空、引用的观察已不在库里的行要计数并照写，不能静默吞掉，
   也不能让预演崩（这两种行在真库上都存在，吞掉就会让普查的分子分母无声失真）。
运行：python3 tests/test_rehearse_writes.py
"""

import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "scripts"))

import rehearse_writes
from test_witness_basis import (  # 同一夹具，不另造一套
    T,
    anchor,
    diff,
    jpeg,
    make_witness,
    mk_obs,
    obs_row,
)

CHECKS = 0
ROW_FIELDS = ("level", "ts_start", "ts_end", "content", "intent", "type", "source",
              "model", "payload", "obs_ids")


def check(cond, msg) -> None:
    global CHECKS
    CHECKS += 1
    assert cond, msg


def rows_of(store, where="1=1", args=()) -> list[dict]:
    return [dict(r) for r in store.conn.execute(
        f"SELECT {','.join(ROW_FIELDS)} FROM messages WHERE level='small' AND {where} "
        "ORDER BY id", args).fetchall()]


def same_rows(a: list[dict], b: list[dict], what: str) -> None:
    """逐字段比两批行：条数与每个字段都要一致，差在哪一条、哪个字段要说出来。"""
    check(len(a) == len(b), f"{what}：行数应相同（{len(a)} vs {len(b)}）")
    for i, (x, y) in enumerate(zip(a, b)):
        for f in ROW_FIELDS:
            check(x[f] == y[f],
                  f"{what}：第 {i} 行字段 {f} 不同：{x[f]!r} vs {y[f]!r}")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# —— a) 落库形状：抽取前后的字段契约 ——
def section_write_shape() -> None:
    with tempfile.TemporaryDirectory() as td:
        store = StoreStub(Path(td) / "shape.db")
        frame = jpeg(Path(td) / "frame.jpg")
        a = mk_obs(store, T, frame)
        b = mk_obs(store, T + 10, frame)
        c = mk_obs(store, T + 20, frame)
        w, _m = make_witness(store, td, [
            anchor("终端在跑测试脚本", importance=0.4),
            diff("继续读报错"),
        ])
        wa = w.process(obs_row(store, a))
        ra = store.conn.execute("SELECT * FROM messages WHERE id=?", (wa,)).fetchone()
        check(ra["level"] == "small" and ra["type"] == "anchor" and ra["source"] == "vlm",
              f"单帧行形状：{dict(ra)}")
        check(ra["model"] == "fake-1" and ra["intent"] == "写代码",
              f"model / intent 应原样落库：{ra['model']!r} {ra['intent']!r}")
        check(json.loads(ra["payload"]) == {"importance": 0.4},
              f"payload 只装模型给的可选字段：{ra['payload']}")
        check(json.loads(ra["obs_ids"]) == [a], f"单帧行 obs_ids：{ra['obs_ids']}")
        check((ra["ts_start"], ra["ts_end"]) == (T, T), f"单帧行窗口：{ra['ts_end']}")

        # 批帧：没有对比句 → 不带基帧；窗口取被引用观察的 min/max
        wb = w.process_batch([obs_row(store, b), obs_row(store, c)])
        rb = store.conn.execute("SELECT * FROM messages WHERE id=?", (wb,)).fetchone()
        check(json.loads(rb["obs_ids"]) == [b, c],
              f"批帧行 obs_ids 只含本轮两帧：{rb['obs_ids']}")
        check((rb["ts_start"], rb["ts_end"]) == (T + 10, T + 20),
              f"批帧行窗口取 min/max：{(rb['ts_start'], rb['ts_end'])}")
        check(rb["type"] == "diff" and rb["content"] == "继续读报错",
              f"批帧行内容：{rb['type']!r} {rb['content']!r}")
        print("write shape OK: process 与 process_batch 经 _write_small 落下的字段逐项符合契约")
        store.close()


# —— b) 预演 = 同一套规则 ——
def section_rehearsal_matches_process() -> None:
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "src.db"
        store = StoreStub(src)
        frame = jpeg(Path(td) / "frame.jpg")
        o = [mk_obs(store, T + i * 10, frame) for i in range(6)]
        w, _m = make_witness(store, td, [
            anchor("终端在跑测试脚本"),
            diff("从终端切至浏览器，MDN 的 flex 章节"),
            diff("相较上一帧，页面滚到报错那一行"),
            diff("仍为同一个编辑器窗口"),
            diff("继续改 store.py"),
            diff("终端里多了一行警告"),
        ])
        for oid in o:
            w.process(obs_row(store, oid))
        store.close()

        # 把这份库当成"冻结快照"喂给预演（预演只认 schema 与行，不认它是谁写的）
        snap = Path(td) / "snap.db"
        shutil.copy(src, snap)
        snap.read_bytes()
        before = sha(snap)
        out = StoreStub(Path(td) / "out.db")
        con = rehearse_writes.connect_ro(str(snap))
        st = rehearse_writes.rehearse(con, out)
        con.close()
        check(sha(snap) == before, "预演不得改快照一个字节")

        real = rows_of(StoreStub(src))
        reh = rows_of(out)
        same_rows(real, reh, "预演 vs process")
        check(st["written"] == len(real), f"预演应写全 {len(real)} 行：{st['written']}")
        check(st["switch_base"] == 3,
              f"两次对比句（含 相较/仍为）都应拿到基帧：{st['switch_base']}")
        print(f"rehearsal OK: {st['written']} 行逐字段与 process 相同，"
              f"快照字节不变，switch_base={st['switch_base']}")
        out.close()


# —— c) 坏输入单独计数 ——
def section_bad_inputs() -> None:
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "bad.db"
        store = StoreStub(src)
        frame = jpeg(Path(td) / "frame.jpg")
        ok = mk_obs(store, T, frame)
        w, _m = make_witness(store, td, [anchor("终端在跑测试脚本")])
        w.process(obs_row(store, ok))
        # 两种坏行：没有 obs_ids 的（老/异常写入），与引用了库里不存在的观察的
        store.insert("messages", level="small", ts_start=T + 100, ts_end=T + 100,
                     content="没有引用的行", intent="继续", type="diff", source="vlm",
                     model="fake-1", payload=None, obs_ids="[]")
        store.insert("messages", level="small", ts_start=T + 200, ts_end=T + 200,
                     content="引用了一帧不存在的行", intent="继续", type="diff", source="vlm",
                     model="fake-1", payload=None, obs_ids="[999999]")
        store.close()

        snap = Path(td) / "snap.db"
        shutil.copy(src, snap)
        out = StoreStub(Path(td) / "out.db")
        con = rehearse_writes.connect_ro(str(snap))
        st = rehearse_writes.rehearse(con, out)
        con.close()
        check(st["rows_in"] == 3, f"三行都应进预演：{st['rows_in']}")
        check(st["empty_obs_ids"] == 1, f"没有引用的行要单独计数：{st['empty_obs_ids']}")
        check(st["missing_refs"] == 1 and st["missing_refs_all"] == 1,
              f"引用丢失的行要单独计数：{st['missing_refs']}/{st['missing_refs_all']}")
        check(st["written"] == 3, f"坏输入不能把行吞掉：written={st['written']}")
        lens = rows_of(out, "obs_ids='[]'")
        check(len(lens) == 1, f"空引用的行应照写进预演库：{lens}")
        print(f"bad inputs OK: 3 行进、{st['written']} 行出，空引用 1、引用丢失 1 各自计数")
        out.close()


class StoreStub:
    """薄包装：导入一次 Store，避免每个测试各自从 backseat.store 抓一次。"""

    def __new__(cls, path):
        from backseat.store import Store
        return Store(path)


def main() -> None:
    section_write_shape()
    section_rehearsal_matches_process()
    section_bad_inputs()
    print(f"\nRehearse smoke: ALL PASS（{CHECKS} 条断言）")


if __name__ == "__main__":
    main()
