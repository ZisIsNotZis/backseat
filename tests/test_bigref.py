"""big 层证据回指冒烟：flashback 按 parent_id 递归下钻汇总叶子 small 的 obs_ids。

覆盖：a) big→medium→small 三层，命中 big 时 refs = 叶子 small 的 obs_ids 并集；
b) 子行 obs_ids 全空的孤儿 big → degraded=True + degraded_reason；
c) 下钻深度有界（超深链截断）且防环（parent_id 自环不死循环）。
d) 同一 big 既挂直连叶子 small 又挂超深链：refs 非空 + degraded=False + reason=chain_truncated；
e) 帧已驱逐且链被截断：reason 保留 frames_evicted，不被 chain_truncated 覆盖。
运行：python3 tests/test_bigref.py
"""

import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from backseat.kb import FLASHBACK_MAX_DEPTH, KB
from backseat.store import Store

sys.path.insert(0, str(Path(__file__).parent))
from test_m2 import mk_obs


def mk_msg(store, level, content, ts, keys=None, obs_ids=None, parent_id=None) -> int:
    """插一行金字塔消息；parent_id 非空即视为已被上级吸收（folded=1）。"""
    return store.insert("messages", level=level, ts_start=ts, ts_end=ts,
                        content=content,
                        keys=json.dumps(keys) if keys is not None else None,
                        obs_ids=json.dumps(obs_ids) if obs_ids is not None else None,
                        folded=1 if parent_id is not None else 0,
                        parent_id=parent_id)


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        store = Store(Path(td) / "t.db")
        kb = KB(store)
        t = time.time()

        def mk_frame(name: str) -> tuple[int, str]:
            """真实存在的帧文件 + 观察行；返回 (obs_id, ref 路径)。"""
            path = Path(td) / f"{name}.jpg"
            path.write_bytes(b"\xff\xd8fake")
            return mk_obs(store, t + len(list(Path(td).glob("*.jpg"))), ref=str(path)), str(path)

        # --- a) big → medium → small：refs = 叶子 small obs_ids 并集 ---
        big = mk_msg(store, "big", "元期：backseat 记忆金字塔", t, keys=["bigref"])
        expected: list[str] = []
        for mi in range(2):
            mid = mk_msg(store, "medium", f"中段{mi}", t - mi, keys=["mid"], parent_id=big)
            for si in range(2):
                oid, ref = mk_frame(f"f{mi}{si}")
                mk_msg(store, "small", f"小段{mi}{si}", t - mi, obs_ids=[oid], parent_id=mid)
                expected.append(ref)
        res = kb.flashback(keys=["bigref"])
        assert len(res) == 1 and res[0]["level"] == "big", res
        assert sorted(res[0]["refs"]) == sorted(expected), res[0]
        assert not res[0]["degraded"] and res[0]["degraded_reason"] is None, res[0]
        print("big refs OK: 三层下钻，big 回指 4 个叶子帧（递归汇总 obs_ids）")

        # medium 直接命中仍走一层，行为未被递归破坏
        res_mid = kb.flashback(keys=["mid"])
        assert len(res_mid) == 2 and all(len(m["refs"]) == 2 for m in res_mid), res_mid
        assert not any(m["degraded"] for m in res_mid), res_mid
        print("medium refs OK: 一层下钻不受影响（既有语义保持）")

        # --- b) 孤儿 big：子行 obs_ids 全空且无更深子行 → 显式降级 ---
        big2 = mk_msg(store, "big", "孤儿元期", t - 10, keys=["orphan"])
        mk_msg(store, "medium", "孤儿中段", t - 10, parent_id=big2)
        res = kb.flashback(keys=["orphan"])
        assert len(res) == 1, res
        assert res[0]["refs"] == [] and res[0]["degraded"] is True, res[0]
        assert res[0]["degraded_reason"] == "no_leaf_obs", res[0]
        print("orphan OK: 递归后仍无证据 → degraded + reason=no_leaf_obs（不静默空集）")

        # --- c1) 深度有界：链长超过上限，最深叶子不可达 → 截断降级 ---
        deep = mk_msg(store, "big", "深链元期", t - 20, keys=["deep"])
        cur = deep
        for i in range(FLASHBACK_MAX_DEPTH + 2):
            cur = mk_msg(store, "medium", f"深{i}", t - 20, parent_id=cur)
        oid, _ = mk_frame("deep")
        mk_msg(store, "small", "深叶子", t - 20, obs_ids=[oid], parent_id=cur)
        res = kb.flashback(keys=["deep"])
        assert len(res) == 1, res
        assert res[0]["refs"] == [] and res[0]["degraded"] is True, res[0]
        assert res[0]["degraded_reason"] == "chain_truncated", res[0]
        print(f"depth bound OK: 超过 {FLASHBACK_MAX_DEPTH} 层即停，返回降级而非递归失控")

        # --- c2) 防环：parent_id 回边指向已访问行（祖先），不死循环 ---
        cyc = mk_msg(store, "big", "成环元期", t - 30, keys=["cycle"])
        a = mk_msg(store, "medium", "环a", t - 30, parent_id=cyc)
        mk_msg(store, "medium", "环b", t - 30, parent_id=a)
        store.conn.execute("UPDATE messages SET parent_id="
                           "(SELECT id FROM messages WHERE content='环b') WHERE id=?", (cyc,))
        store.conn.commit()
        res = kb.flashback(keys=["cycle"])
        assert len(res) == 1, res
        assert res[0]["refs"] == [] and res[0]["degraded"] is True, res[0]
        assert res[0]["degraded_reason"] == "chain_truncated", res[0]
        print("cycle guard OK: parent_id 回边指向祖先被 seen 拦住，不无限递归")

        # --- d) 部分可达：直连叶子 small 有 obs_ids，同时挂一条超深链 ---
        mixed = mk_msg(store, "big", "混合元期", t - 40, keys=["mixed"])
        direct_oid, direct_ref = mk_frame("mixed_direct")
        mk_msg(store, "small", "混合直连叶子", t - 40, obs_ids=[direct_oid], parent_id=mixed)
        cur = mixed
        for i in range(FLASHBACK_MAX_DEPTH + 2):
            cur = mk_msg(store, "medium", f"混合深{i}", t - 40, parent_id=cur)
        deep_oid, _ = mk_frame("mixed_deep")
        mk_msg(store, "small", "混合深叶子", t - 40, obs_ids=[deep_oid], parent_id=cur)
        res = kb.flashback(keys=["mixed"])
        assert len(res) == 1, res
        assert res[0]["refs"] == [direct_ref], res[0]  # 深链被截断，只回指可达的直连叶子
        assert res[0]["degraded"] is False, res[0]
        assert res[0]["degraded_reason"] == "chain_truncated", res[0]
        print("partial refs OK: 直连叶子可达 + 深链截断 → refs 非空、不降级、reason 仍报截断")

        # --- e) 帧已驱逐 + 链被截断：frames_evicted 不被 chain_truncated 覆盖 ---
        evicted = mk_msg(store, "big", "驱逐元期", t - 50, keys=["evicted"])
        gone_oid = mk_obs(store, t - 50, ref=None)  # 行保留、ref 为 NULL = 已驱逐
        mk_msg(store, "small", "驱逐直连叶子", t - 50, obs_ids=[gone_oid], parent_id=evicted)
        cur = evicted
        for i in range(FLASHBACK_MAX_DEPTH + 2):
            cur = mk_msg(store, "medium", f"驱逐深{i}", t - 50, parent_id=cur)
        deep_oid, _ = mk_frame("evicted_deep")
        mk_msg(store, "small", "驱逐深叶子", t - 50, obs_ids=[deep_oid], parent_id=cur)
        res = kb.flashback(keys=["evicted"])
        assert len(res) == 1, res
        assert res[0]["refs"] == [] and res[0]["degraded"] is True, res[0]
        assert res[0]["degraded_reason"] == "frames_evicted", res[0]
        print("evicted reason OK: 帧已驱逐 + 链截断 → reason=frames_evicted（更可操作的原因不被吞）")

        store.close()
    print("\nbigref smoke: ALL PASS")


if __name__ == "__main__":
    main()
