kind:    report
from:    backseat
to:      root
spend:   machine_time idle class; 50 grounded model calls before metering landed (unmetered), 1 metered call 6,870 in / 294 out / 4.6 s
id:      answers the 2026-10-23 direction-change letter with the user's 2026-10-27 "Proceed … Redo roadmap if needed"; decision: none waiting except the human thesis verdict

# M0″ 已挂为主线并交到机械门；只剩题面正确率这一道人的门

## 结论

用户 2026-10-27 的指令被当作方向变更的那一句授权：**M0″（题面→外部材料→评论）成为当前主线**，
M0′ 的门槛降为见证层性质，Release 2.0 转维护态。roadmap 已重写并推送。
一条端到端链已交付：触发（`observations.meta.to` / 新 small）→ 本机项目解析 → 本机仓库与 pi 会话材料
→ 带引用评论 / 沉默。**机械三门全绿；题面正确率未判，等你（用户）看 50 行抽样表。**

## 可复算的读数（2026-10-27）

    python3 tests/test_grounding.py
    → ALL PASS（125 条断言）

    python3 scripts/m0pp_eval.py --decisions ~/backseat-data/grounding
    → 决策数=50  speak=50  silence=0
      有据率=100.0% (50/50)
      无材料出口数=0
      错题数（跨项目引用）=0
      取材料后文件被改过（stale，仍算可解析）=2
      题面正确率=N/A（未给 --labels）
      判定=PASS   退出 0

    其余 14 个测试文件逐个直跑，无 FAIL。

一条新 demo（计量落地后）：

    python3 scripts/m0pp_demo.py --title 'zsh · ~/vibe/backseat' --data-dir /tmp/m0pp-met
    → speak；metrics 一行 grounding:comment：6,870 in / 294 out / 4,646 ms；
      决策 usage 同值；trajectory/2026-10-27.jsonl 落同一调用。

那 50 次批量调用发生在计量落地前，**未计量**，没有事后补数。上面的单次实测只作量级参考，
不是那批的账。

## 人的那门：只看题面，不看流畅度

抽样表：`~/backseat-data/grounding/sample.md`（50 行，28 KB）。最后一列空着，填 `正确` 或 `错误`：
判“题面说对了在哪个项目/哪一步”。填完：

    python3 scripts/m0pp_labels.py --sheet ~/backseat-data/grounding/sample.md \
        --out ~/backseat-data/grounding/labels.json
    python3 scripts/m0pp_eval.py --decisions ~/backseat-data/grounding \
        --labels ~/backseat-data/grounding/labels.json

表头写明了这个 dry-run 的两个边界：批量时刻来自 `~/.pi/agent/sessions/` 里**真实的 role=user 消息**，
代替实时窗口标题（采集自 2026-10-21 停着）；50 行全在 2026-10-27 12:50–13:36Z 这一段。
不造时刻、不替人判。

## 这次落地了什么（细节只给指针）

- 代码：`backseat/grounding.py`、守护进程挂点（`grounding_cursor` + 30 分钟取材料节流 + 标题扫全行
  obs_ids 后 30 分钟近前兜底）、`scripts/m0pp_{demo,eval,sample,labels}.py`、`tests/test_grounding.py`。
- 契约与命令：`docs/7_INTERFACE.md` 的 Material/source_refs/决策 JSONL/usage，`docs/9_IMPLEMENTATION.md`
  的演示、判官、抽样、labels、计量；阈值只在 `docs/0_ROADMAP.md`。
- 发布面：README 两语首屏去掉了“接过方向盘”的空头承诺，改为“屏幕当触发器、材料在屏幕外、无材料沉默”，
  测试清单 15 个，并给出 M0″ 三条命令。

## 没做的、仍开着的

- **题面正确率未判**（上面那 50 行）；在用户填表之前不写“M0″ 通过”。
- **实时标题触发没有真实屏幕读数**：采集仍停着。起采集＝常态运行、持续花网关配额，这是 roadmap
  「等你一句」第 4 条，仍未答；我没有替用户开。
- **网络材料未接**：第一个通道只取本机（仓库 + pi 会话），离线可复算；公开资料是后接通道。
- 其余旧待确认项（M1 实跑口径、M3/M4/M5 的确认门、C2 取舍、Release 2.1/2.2 预告）不阻塞 M0″，
  状态未改；M0′ 的 B 抽样与拒收率正式读数仍是“不能确立”，没有借这次交付改写。

## 要你做的只有一件

看 `sample.md` 的 50 行题面，填最后一列。有这一列，M0″ 才算有人的判定；没有它，机械门再绿也只是
“引用了什么”成立，不是“认对了题”成立。
