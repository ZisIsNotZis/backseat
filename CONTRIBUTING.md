# 贡献指南

## 方法论（先读）

- **事实归引擎，风格归人格**：见证层/压缩层只产出事实；任何"怎么说"都是 Persona 插件
- **确定性算法能做的，模型不碰**：pHash 门控、去重、折叠是纯代码；LLM 只出现在 L1-L3 的进场线上
- **无魔法数**：运行参数都是弹性预算（storage_bytes / daily_requests / effort）的导出值
- **表结构描述引擎本体**：插件不是表，是字段值（sensor / channel / persona id）

## 工程纪律

- 新行为先进 [docs/DESIGN.md](docs/DESIGN.md) 决策账本（Grill Round），再实现
- schema 改动 = 迁移；语义契约改动 = 更新 [docs/SCHEMA.md](docs/SCHEMA.md)
- 每个里程碑的冒烟测试放 `tests/test_mN.py`，风格为无依赖直跑脚本
- LLM 调用必须落 trajectory JSONL + metrics 表（成本可见性是产品功能，不是调试日志）

## 提交

- 一个 commit 一个完整关注点，注明实测证据（测试输出/成本数据）
- AGPL-3.0，移植代码须同源同许可
