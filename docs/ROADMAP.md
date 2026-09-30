# Backseat Roadmap

## Release 2.0 — 感知层（当前）

范围：**仅感知层 + 弹幕出口**。帮助层不做。每个 Milestone 结束有确认门（⏸），需要用户确认后才进入下一个。

### M0 骨架与数据层
- 事件总线（asyncio，全时间戳，墙钟+单调双轨）+ SQLite schema：`observations / messages / kb / expressions / metrics / state` 六张表（messages 单表 level=small/medium/big，汉诺塔合并）（state=kv：USER_MODEL、ANCHOR、计数器、预算水位）（metrics=逐调用 tokens/缓存/时延，M0 定稿时纳入）
- 配置文件（storage_bytes、daily_requests可选、effort、K/M/N、节流、模型路由）——全部为弹性输入，引擎推导运行参数
- ⏸ **确认门：schema 评审**——六张表（DESIGN §10.G1 三表合一 + M0 定稿纳入 metrics）的字段定稿，之后改表要迁移【已确认 2026-09-18】

### M1 传感器（L0）
- 屏幕采集（x11grab）+ pHash 门控 + 空帧拦截 + 大事件检测（封闭枚举：窗口切换/workspace/全屏/恢复空闲）
- 采样节奏控制器：动力学EMA分档（60s/20s/2s+批帧），大事件直通
- 活动窗口标题采集；捕获排除清单生效（若 Q1 决定启用）
- ⏸ **确认门：实跑一天**，检查事件流的召回/误报率（同时报告：捕获排除清单命中次数，若启用）

### M2 见证层（L1）
- 模态路由：ATSPI文本 > VLM转写 > VLM描述
- anchor/diff 双模式 + 强制重锚（N帧事件或锚龄30min）
- 严格 JSON schema（语义层：type/diff/intent/candidate_project?/importance?/roast?，无介质词汇）+ **固化 docs/SCHEMA.md**（Event/Mini/Expression schema、插件契约、mood闭集、status枚举 sent→shown→…）
- 逐调用 token/成本/缓存命中/时延计量 + **Trajectory 全量 JSONL 落盘**（开发期调试生命线）
- **门内必报实测数据**（前缀缓存命中率、每帧均摊成本、存储水位增量），供用户校准预算算法
- ⏸ **确认门：用户抽查一天的 mini 质量** + 成本实测报告（无硬顶，纯校准用）【已确认 2026-09-28：mini/mid 质量在线；模型换 github_copilot/gpt-6-luna（effort=low），拼批后 ~265 out tok/帧、p50 8s】

### M3 记忆金字塔（L2）
- minis→mid（K条或大变化）、mids→big（M条）
- USER_MODEL 增量合并；重启恢复（bigs+mids+anchor）
- ⏸ **确认门：重启验收**——重启后第一轮答对"用户在干什么"；连续两天的 mid 摘要用户认可

### M4 唤起记忆（KB + flashback）
- keys 双重身份、`!always`/`!at`、提及自动注入、last_shown 防重
- flashback 查询（逐字回放 + 帧引用 + 降级）
- ⏸ **确认门：callback 演示**——同模式事件再现时，历史自动到场

### M5 人格层 + 出口：弹幕归位
- **人格层（第一个 Persona 插件「弹幕君」）**：订阅事实流+KB命中+USER_MODEL，独立冻结前缀（人设+记忆引擎说明段），产出表达草稿（content/mood/salience）
- 引擎闸门：节流（min_roast_interval）+ expressions 去重 + 沉默权，施加于草稿后
- 现有 danmaku_engine 移植为第一个 Outlet 插件（15px、顶行优先、紧贴、双行大字、点击穿透；mood/salience → 颜色/字号映射）
- ⏸ **确认门：真实日常跑48小时**，弹幕质量与频率用户打分

### M6 打包发布
- 一键安装（venv + systemd user service / 手动脚本）、README 终稿、GitHub 发布
- AGPL-3.0 LICENSE、贡献指南
- ⏸ **确认门：全新机器安装成功**（用户亲自装）→ **打 tag release-2.0**

## Release 2.1 — 感知层增强（预告）

- **输入流可插拔（Backlog，用户 2026-09-29 提出）**：传感器源不写死屏幕——任意帧流
  （RTSP、图片目录/序列、另一个 X display、视频文件）都应能接进 L0。现在的
  `config.display` 只是第一步；抽象成 `Source` 插件（对齐 DESIGN §5 的 Sensor 契约），
  让测试/演示/CI 不再依赖真实桌面

- ATSPI 常驻传感器（终端/编辑器文本直取）、D-Bus 仅 opportunistic（只记真实到达的桌面通知，不承诺成败覆盖）
- 深潜策略（L3 触发封闭枚举：首次目击/重大变化/长gap恢复/AI coding爆发）
- 侦探层（bwrap 只读沙箱、12轮、investigate 召唤）
- pin 帧机制与预算驱逐
- VLM 本地化选项（ollama + 本地 VLM 全离线运行）

## Release 2.2 — 第二张嘴

- 系统通知出口、CLI 查询出口（`backseat ask "我这周干了啥"`）
- 用户画像导出（JSON/Markdown，为组织级愿景预留）

## Release 3.x — 帮助层（北极星第一阶段）

方法论（落地前单独成文打磨）：

1. **提案流**：画像层检测重复模式（同一动作序列≥N次无失败）→ 生成提案 → **必须用户确认**——"我发现你经常这么干，你想达成的是不是X？我能做个工具帮你，方案是这样，行不行？"
2. **分级阐述**：按用户背景调整表达——非程序员/小孩听得懂的方案描述；细节谁定（系统定 or 用户定）由用户专业度路由
3. **执行**：批准后由执行后端完成（自研只读/白名单工具循环起步；agent 框架适配器可选）
4. **回访**：执行后主动回访效果，"好不好用"，结果写回画像
5. **垂直开发纪律**：需求追踪、业务理解落盘、设计/开发/调试原则、agent spawn policy——参考 AGENTS.md 方法论，形成 Backseat 自己的 `help-policy` 文档

## 北极星（不排期）

个人画像可导出 → 多实例互认 → 组织级记忆（team/部门/公司）→ 集体痛点理解与解决。个人痛点是片面的，集群理解才能彻底解决痛点。数据模型从 M0 起为此保持干净。
