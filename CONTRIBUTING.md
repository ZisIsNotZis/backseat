# 贡献指南

方法与纪律的正文只有一处：工程纪律与提交规范住 [docs/9_IMPLEMENTATION.md](docs/9_IMPLEMENTATION.md)，
设计决定住 [docs/6_ARCHITECTURE.md](docs/6_ARCHITECTURE.md) 的决策记录，契约改动住
[docs/7_INTERFACE.md](docs/7_INTERFACE.md)，写入规则改动住 [docs/8_DETAIL.md](docs/8_DETAIL.md)。
本文件只留进门要读的四句：

- **事实归引擎，风格归人格**：见证层/压缩层只产出事实；任何"怎么说"都是 Persona 插件。
- **确定性算法能做的，模型不碰**：pHash 门控、去重、折叠是纯代码；LLM 只出现在 L1–L3 的进场线上。
- **无魔法数**：运行参数都是弹性预算（`storage_bytes` / `daily_requests` / `effort`）的导出值。
- **表结构描述引擎本体**：插件不是表，是字段值（`sensor` / `channel` / persona id）。

改行为前先把决定写进对应层级（新行为 → [6_ARCHITECTURE.md](docs/6_ARCHITECTURE.md) 的决策记录；
schema 改动 = 迁移并改 [7_INTERFACE.md](docs/7_INTERFACE.md)；语义契约改动 = 改同一份契约并补测试）。

## 提交

- 一个 commit 一个完整关注点，注明实测证据（测试输出/成本数据）。
- AGPL-3.0，移植代码须同源同许可。
