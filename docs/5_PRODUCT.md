# 5_PRODUCT — 产品层：开口的规矩与呈现

这一层写他眼里的产品：它开口的规矩、一句话长什么样、什么时候把话咽回去，以及人格层与引擎在体验上
怎么分开。身份、目标、非目标与北极星住 [2_GOAL.md](2_GOAL.md)；数据结构与字段住
[7_INTERFACE.md](7_INTERFACE.md)；链路、采样与合并策略住 [6_ARCHITECTURE.md](6_ARCHITECTURE.md)；
证据引用、删除感知、会话边界与 flashback 住 [8_DETAIL.md](8_DETAIL.md)；门槛阈值住
[0_ROADMAP.md](0_ROADMAP.md)。

# EXPLICIT

## 它说什么

说话的身份是评论者与人肉建议者，不承诺动作；这句原话与来源住 [2_GOAL.md](2_GOAL.md)「它说什么」。
这一层落的是它的产品规矩：开口的唯一资格（信息不对称），以及什么场合把话咽回去。

## 说话的唯一理由：信息不对称（用户拍板 2026-10-05）

**系统知道而用户不知道的东西。** 其余一律不说。

- 屏幕上的内容用户自己看得见，复述（剧情/画面/正在做什么）没有价值，属废话。
- 系统独有的优势是跨时间的眼睛：重复（这是第 N 次）、间隔（隔 X 天又出现）、累积（第 X 天）、矛盾与遗忘、异常。
  确定性挖掘器（backseat/insight.py，无 LLM）产出候选，人格层据此表达；无候选则沉默（含周期 digest）。
- 产品定位是助手（学习→理解→kick in→propose），不是氛围组；氛围组是显式模式（persona_mode = companion），默认 assistant。
- 人格契约明令禁止复述可见内容；mood 按场景分配（日常 neutral/focused），避免只会毒舌的单边化。

# CONFIRMED

本层没有落在这个级别的条目。已拍板的产品规矩全在上一区（用户自述与用户拍板）。

# INFERRED

## 一条表达的生命周期（体验侧）

触发 → 题面 → 外部材料 → 评论，四段正文与四笔代价（网络、延迟、陈旧、认错题）住
[2_GOAL.md](2_GOAL.md)“物质从哪来”。体验侧只剩三条后果：材料取不到时不开口（沉默或降级，不编）；
一条话不再秒级；说错题（拿 A 项目的材料评 B）比不说更贵。

一条话真被说出来，要过人格层的草稿和引擎的闸门：人格层先把事实说成人话（内容 + mood + salience），
引擎再判这句该不该出门。落库的字段与 `status` 走 [7_INTERFACE.md](7_INTERFACE.md)；成功与否只看出口回执。

### M0″ 的体验规矩（2026-10-27 授权后）

- **先有材料，才有话。** 一条表达在用户眼里必须先站得住：它引用的材料在库里可查（locator、取的时刻、
  内容哈希）。用户看不到“我觉得”，只看到“这一点来自哪里”；材料取不到时，出口是 0 条，不是换一种说法。
- **说错题比不说贵。** 材料属于 A 项目而题面是 B 项目，这条表达直接判错并丢弃；同一句话说得再顺也不补分。
  这条是产品规矩，不是评测口味：拿别的项目的材料评价他，会让他不再看第二眼。
- **题面只要一句。** 一句“他在哪个项目/哪一步”先立住，材料才有落点；题面解析不出，就不进入评论。
- **慢是代价，不是故障。** 取材料要时间，一条表达不再是秒级；沉默期间不拿旧材料凑数，材料带 `fetched_at`，
  过期就重新取。
- **人的那门是题面正确率**：抽样 ≥50 个实际时刻，由用户判“说对了在哪个项目/哪一步”，不看流畅度。
  机器只保证引用与沉默，不替用户说自己懂了。

## mood / salience 到介质的映射

mood 是闭集、salience 是 0~1 连续值（名单与取值口径住 [7_INTERFACE.md](7_INTERFACE.md)）；出口插件声明自己
支持哪几个 mood，未知的降级 neutral。**介质翻译（颜色、字号、是否单行、落在第几行、滚动速度、打断等级）
是出口插件对 mood/salience 的映射——弹幕不是词汇，是翻译结果。** 一次具体实例：mood=frustrated →
`#FF4D4D`，salience 0.7 小于单行阈值 0.85 → 单行、第 1 行、322px/s（见本文件走查 T4）。完整映射表属于
出口插件自己，不在这里另立一份。

两个重要度不是一回事：mini 的 importance 是事件重要度（驱动 pin 与深潜），expression 的 salience 是发言
重要度（驱动字号与打断等级），同名两义已拆开（见 [7_INTERFACE.md](7_INTERFACE.md)）。

## 闸门：节流 / 去重 / 沉默权

- **节流**：同一出口在最小间隔内不再发（走查配置里是 `min_roast_interval=10s`；同样的事走查 T4 里以“昨日 speak”为例放行）。
- **去重**：与已发过的内容按重叠判定，重叠够小才放行（走查里 8% 重叠放行、60% 才算重复；`dedup_key` 落库）。
- **沉默权**：默认不说话。没有候选、没有材料、人格调用失败、闸门拦下，全是正常的沉默，不是故障——
  走查整夜的账（本文件「全场账本」与 T9）就是一次完整实例：采样极多、调用极少、说出口只有几条。
- **回执才算数**：`sent` 只是已投递，出口逐条回执推进到 `shown`（落位成功）或 `skipped`（无空余 lane）；
  `skipped`/超时/旧引擎无回执一律记 `sent`，不谎报 `shown`。字段与枚举住 [7_INTERFACE.md](7_INTERFACE.md)。

## 人格层与引擎的体验分界

事实归引擎（见证、压缩、侦探只产事实），风格归人格（订阅事实 + KB + USER_MODEL，独立冻结前缀，独立 LLM 调用，
只产表达草稿）；N 个人格 = N 个实例。引擎闸门施加在草稿之后。他因此看到的是：同一份事实可以被不同人格说成
不同味道的话，但每句都过了闸门；事实与刻薄在账上分开；人格调用失败时这一轮就是沉默，不会退化成引擎硬说。

人格的语言：V2 中文优先；英文人格是独立的插件文件，发布前补。

## 走查剧本：Backseat 工作流干燥运行剧本 v4

以下全文是交互逻辑与期望输出的剧本。剧本里的机制细节各有正文归属（本处只给指针，不复述）：
链路与采样/合并策略 → [6_ARCHITECTURE.md](6_ARCHITECTURE.md)；字段、`status` 枚举、mood 闭集与回执映射 →
[7_INTERFACE.md](7_INTERFACE.md)；语义层证据引用、振荡签名、会话边界 `gap_close_min`、flashback 深度 →
[8_DETAIL.md](8_DETAIL.md)；门槛阈值 → [0_ROADMAP.md](0_ROADMAP.md)。

经三轮独立审查与四轮 grill 修订。与设计文档冲突处以后者为准并修文档。

**示意 id**：`s*=small消息, m*=medium, b0=big, o*=observations, x*=expressions, k*=kb`。rowid 由 SQLite 分配。
**配置**：`20G, effort=0.5` → `处理节奏20s / 锚龄30min重锚 / 侦探枚举触发可用(召唤关,8轮) / burst批窗30s`；`K=20, M=10, min_roast_interval=10s, gap=30min, hamming≥6入选`。
**传感器**：仅 screen（ATSPI 属 2.1）。
**分层**（Round 4 修订）：L0 采样+挑选（无LLM）→ L1 见证（**纯事实**）→ L2 合并（纯事实）→ **人格层（插件，独立LLM，只管怎么说）** → 引擎闸门 → 出口。侦探 L3 只读，产出文本交引擎写库。

### 全场账本

| 段 | 时段 | 档位 | 采样(1s) | 入选 | L1调用 | small |
|---|---|---|---|---|---|---|
| S0 | 19:58-20:03 | 低60s | 5 | 0 | 0 | 0 |
| S1 | 20:03:14 | 大事件 | 1 | 1 | 1 | s1 |
| S2 | 20:03-20:15 | 中20s | 720 | 38 | 20 | s2-s21 |
| S3 | 20:16-20:31 | burst | 900 | 170 | 30批 | s22-s51 |
| S4 | 20:31-21:30 | 中 | 3540 | 260 | 57 | s52-s108 |
| S5 | 21:31-22:41 | 中→低 | 1200 | 55 | 12 | s109-s120 |
| S6 | 22:41-23:40 | 低 | 60 | 0 | 0 | 0 |

采样 6426 → 入选 524（RAM环挑选，宁多勿漏）→ L1 调用 120；人格层调用 ~15；L3 深潜 1；合并 6 次（m1@20:11:47, m2@20:29:20, m3@20:49:30, m4@21:09, m5@21:29, m6@23:11 gap_close）。表达 speak×4（弹幕3 + CLI×1【二期预览】）。

### T0 【M1】19:58 启动，屏幕静止

```
L0  低档60s采样进RAM环；5帧hamming=0 → 入选0。零落表零调用
    （无锁屏事件：屏幕不动=没事发生）
```

### T1 【M1+M2】20:03:14 用户出现（大事件直通+anchor+升档）

```
L0  WM标题变→大事件直通抓帧 o101{sensor:screen,kind:screen.notable,display:primary,
    key:phash:3fa2…,ref:200314.jpg,pinned:0,meta:{to:"zsh · ~/vibe/backseat"}}；升中档(处理20s,采样1s)

L1  调用#1 mode=anchor。context（冻结前缀缓存冷启动）：
    [system 见证层（冻结）：事实压缩契约 + 记忆引擎说明段]
      记忆引擎说明段（固定文字，防遗忘/防幻觉）：
      "USER_MODEL=你对用户的当前理解（知识·名词）；MEDIUM/BIG=历史纪要（历史·动词）；
       [KB命中行]=与当前相关的长期知识自动浮现；SMALLS=事实流水。均为引擎注入，非你生成"
    [USER_MODEL] {"projects":["mycar"],…}                ← 无 backseat
    [BIGs] b0（昨日）  [MEDIUMs] 昨日×2 未折叠  [SMALLs] 0
    [KB命中行] 无      [expressions尾部] 昨日x(-3)~x(-1)
    [本轮] {"now":"20:03:14 周四","mode":"anchor","images":[1张]}   ← 时钟最后
    输出（纯事实，无roast字段）：
    {"type":"anchor",
     "describe":"终端在~/vibe/backseat；README标题'Backseat——本地优先AI长期伴侣'",
     "intent":"开始一个画像里没有的项目，业务待确认",        ← 认知谦卑
     "candidate_project":"backseat",                      ← 深潜触发输入
     "importance":0.3, "note":"新项目首见"}
落表 o101 + s1(obs_ids=[o101])
```

### T2 【2.1】20:03:20 深潜（侦探只读，引擎是唯一写入者）

```
调度器规则①：s1.candidate_project ∉ USER_MODEL.projects → L3
L3  侦探（bwrap只读+断网[待定]，effort=0.5→召唤关/枚举触发可用/上限8轮，实际3次工具）：
    read README.md → read pyproject.toml → git log|head
    产出=纯文本档案 → 交引擎，引擎写：
    k7 {keys:["backseat","记忆引擎","danmaku","store.py"],
        desc:"长期伴侣系统：屏幕观察→分层记忆，弹幕是输出口之一，AGPL", kind:"fact"}
    state.USER_MODEL.projects+="backseat"；o101.pinned=1+ref_full全分辨率副本
    侦探产物不进 messages 流（不是活动证据，不占K名额）

谦卑对照：s1（潜前）"业务待确认" → s3（潜后,k7在前缀）"写backseat的存储层messages表（k7）"
```

### T3 【M1+M2】20:05~20:11 写代码（中档稳态）

```
L0  采样1s进环；挑选器每20s处理窗从环中选hamming≥6的帧（≤6张按时序）
L1  20次事实调用：
    s4 {"type":"diff","diff":"store.py新增messages建表语句",
        "intent":"写backseat存储层（k7）","importance":0.2}
    s19 {"type":"diff","diff":"pytest：3 failed——test_m0.py外键断言失败",
         "intent":"跑测试挂了","importance":0.7}          ← 事实层只记录，不评价
落表 o102~o121（未入选帧留RAM环自然淘汰）+ s2~s21
```

### T4 【M2事实+人格层首秀+M5闸门】20:11:20 KB命中→callback

```
见证层 s20（事实）：{"diff":"仍在修外键","intent":"继续","importance":0.7}
KB   s20提及文本含"pytest" → 命中 m0(14:02, keys=["pytest","最后一次改"]) → 注入：
     [KB命中行] "14:02 场景:修同一外键bug | 弧线:三次声称'最后一次改' | 事件:预告全落空"
     （m0.last_shown=20:11:20；同key 60min不重复注入）

人格层触发：importance≥0.7 且 KB命中 → 「弹幕君」插件独立调用：
    [system 弹幕君人格（冻结：人设+风格+记忆引擎说明+表达契约）]  ← 另一份冻结前缀
    [USER_MODEL快照] [新事实 s19,s20] [KB命中行 m0]
    [expressions尾部 昨日x(-3)~x(-1)]                      ← 人格知道自己说过什么
    [本轮] {"trigger":"importance+KB命中","now":"20:11:20"}
    输出草稿：{"speak":{"content":"下午两点就说'最后一次改'，晚上八点还在改——
              '最后'是你用过最贵的词","mood":"frustrated","salience":0.7}}
    trajectory落盘：{"ts":"20:11:20","purpose":"persona:danmaku",
      "messages":[…全量…],"response":{…含reasoning…},
      "usage":{"in":1843,"cached":1502,"out":96},"latency_ms":3800}

引擎闸门：节流(昨日speak ✓) → 去重(重叠8%<60% ✓) → 放行
落表 x1 {kind:"speak", content:…, channel:"danmaku", mood:"frustrated",
         salience:0.7, dedup_key:"最贵的词",
         source_refs:[{messages,s20},{messages,m0}], status:"sent"}
出口（danmaku）：mood→#FF4D4D；salience0.7<0.85→单行；行分配第1行；
    322px/s；渲染回执→status:"shown"
```
> 事实（见证层）与刻薄（人格层）从此分离；N个人格=N个插件实例。

### T4.5 【M1+M2】20:13:05 删除感知（像素引擎的振荡签名）

```
L0  hamming序列 12→9→15 且 frame_i 与 frame_{i+2} 相似度回升0.82
    → 振荡签名=疑似删除/回退 → 挑选器按序取3帧（删前/删中/删后）强制成批
L1  s21（3图序列批）：
    {"type":"diff",
     "diff":"删除了cascade参数又重写——注意：净变化小但发生实质删除+重写",
     "intent":"推翻刚才的写法重来","importance":0.4}
    （若静默采样10s一帧，这次删除+重写会伪装成"没变化"——序列批帧让它无所遁形）
```

### T5 【M1 burst + M2 批帧 + 2.1 pin】20:16~20:31

```
L0  浏览器MDN/GitHub连环切，EMA超阈→burst：采样1s、批窗30s（每窗按phash多样性选6张拼网格）
L1  30次批调用：s44 {"diff":"时间线：MDN→sqlite issue#41→终端，反复对照",
                     "intent":"查ON DELETE语法","importance":0.4}
重锚 20:33锚龄30min→type=anchor重锚（anchor_every_n未到，锚龄先触发）
pin  20:29:52全绿帧 o118{pinned:1,ref_full}；s45 importance=0.8
     （importance=事件重要度→pin；expression.salience=发言重要度→字号，两码事）
人格层 x2 {kind:"speak",content:"绿了。这条外键值得进档案",mood:"happy",
           salience:0.5, channel:"danmaku", status:"shown"}
```

### T6 【M3】20:11:47 首次合并（全夜6次同机制）

```
D   未折叠small=20≥K → L2纯文字压缩：
    m1 {content:"20:03~20:11重构backseat存储层：建messages表，pytest三连败",
        keys:["backseat","外键","pytest","store.py"],
        callback_tags:["测试失败宣言"], events:["首见深潜","pytest红字"],
        user_model_delta:{skill_note:"SQLite外键不熟"}}
落表 m1；s1~s20 folded=1 parent_id=m1（不删，可下钻）；session_stream清空→
    下次调用重建缓存（唯一断点）；USER_MODEL⊕delta
    m2@20:29:20、m3/m4/m5 顺延（m3/m4 content 记录"存储层收尾全绿，计划转M1传感器"）
```

### T7 【2.2 二期预览】21:30 CLI 问答（一期无输入——本项目无输入框/音响/麦克风）

```
你：$ backseat ask "我今晚干了啥"        ← 二期才有的STDIN/CLI通道
F   显式查询：时间范围+keys模糊 → 命中 m1/m2/m5 → 受控合成调用（计入调用数与trajectory）
    输出："20:03重构存储层；外键三连败（MDN自救）；20:29全绿收工。"
落表 x4 {kind:"speak", channel:"cli", source_refs:[m1,m2,m5], status:"shown"}
```

### T8 【M2】21:31 人格层周期digest（触发：5min事实积累）

```
人格层 digest调用：输入=近5min事实(s109~s111)+USER_MODEL
    草稿 x3 {content:"今晚剧情完整复盘：三次假结局，一个真结局",mood:"mocking",salience:0.5}
    → 闸门放行 → 弹幕
```

### T9 【M1/M3】22:41 挂机 + 23:11 gap_close

```
L0  hamming=0 → 低档退避，12帧全拦
D   gap>30min → 强制闭合：m6 折叠 s102~s120（19条，不足K也强制）
    {content:"21:31~22:41存储层收尾转M1传感器规划，22:41起挂机",
     keys:["backseat","M1","传感器"], user_model_delta:{next:"M1传感器"}}
```

### T10 【M3验收】次日 09:00 重启

```
前缀重建：system(冻结)→USER_MODEL→b0→m1~m6→SMALLs(0)→KB(无)→expressions尾部→本轮
第一帧问答："昨晚存储层收尾全绿，说转M1传感器——现在屏幕是backseat的传感器文件" ✓
    每个短语溯源到 m5/m6/k7
```

### Trajectory 与实时统计（开发期生命线）

```
~/backseat-data/trajectory/2026-09-17.jsonl   每次LLM调用一行全量dump：
  {ts, purpose, model, messages[全], response{content,reasoning}, usage{in,cached,out}, latency_ms}
metrics表（逐调用）：purpose | tokens_in/out | cached_tokens | latency_ms
state水位（每10min）：storage_bytes_now / delta / 帧数 / 调用数
夜账单（23:40自动汇总入trajectory尾行）：
  L1 120次 / 人格15 / L3×1(3工具) / 合并6
  tokens: in 214K (cached 71%) / out 14.6K
  存储: 79MB+pin 2.4MB（水位 0.4% of 20G）
  表达: 4条speak；节流拦2；沉默权106次
  ——面向终端用户时，这份账单就是成本预估与预算反调的原料
```

### 公理对照（设计公理）

公理的正文住 [6_ARCHITECTURE.md](6_ARCHITECTURE.md)；本处只是走查时的对照索引。

1. 事实归引擎，风格归人格（T4双调用结构）
2. 安静是常态：6426采样→120调用→4句话
3. callback是机制：keys命中→注入→人格接梗（T4）
4. 认知有边界：candidate_project→深潜→此后才可业务定性（T1/T2/T3）
5. 删改不隐身：振荡签名+序列批帧（T4.5）
6. 采样率≠处理率：1s采、20s处理、批帧拼装，细节密度与调用数解耦（T5）
7. 合并不删原文：folded+parent_id全链下钻（T6）
8. 缓存只在合并边界断；记忆引擎对模型可见且说明固定（缓存安全）
9. 侦探只读、引擎唯一写入者（T2）
