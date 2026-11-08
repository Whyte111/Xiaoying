# 03 评估

## 1. 怎么测

- 单元/集成：`unittest`（标准库，零依赖）
- 端到端：真实 CLI 调用 + 落盘产物，产物在 `examples/`
- 模型决策：真模型跑两条路径，检查 trace 中动作是否随工具结果变化

```bash
python -m unittest discover -s tests -t . -v
```

结果：**52 项，全绿**（2026-11-08 实跑）。

| 文件 | 数量 | 覆盖 |
|---|---|---|
| `tests/test_minloop.py` | 18 | 领域规则、时段、门控、存储幂等、端到端六项 |
| `tests/test_llm_adapter.py` | 9 | 模型适配（假 transport）、离线 Mock 决策 |
| `tests/test_robustness.py` | 25 | 输入校验、工具故障、提前结束、回滚、注入、CLI 兜底 |

## 2. 用例 × 验收点矩阵

| 用例 | 产物 | 运行状态 | 验收点 |
|---|---|---|---|
| M05 正常 | `examples/m05-normal.json` + `traces/m05-normal.jsonl` | `DONE` | 正常排期 |
| M01 冲突 | `examples/m01-conflict.json` + `traces/m01-conflict.jsonl` | `NEEDS_CONFIRM` | 时段冲突、确认边界 |
| M01 重复 | `examples/m01-duplicate.json` + `traces/m01-duplicate.jsonl` | `SAVED_IDEMPOTENT` | 重复提交 |
| M02 缺信息 | `examples/m02-missing-info.json` + `traces/m02-missing-info.jsonl` | `NEEDS_INFO` | 素材信息缺失 |
| M03 warn | `examples/m03-rule-warn.json` + `traces/m03-rule-warn.jsonl` | `NEEDS_CONFIRM`（`WARN`） | 规则风险 |
| M04 block | `examples/m04-blocked.json` + `traces/m04-blocked.jsonl` | `BLOCKED` | 规则风险（阻断） |
| M01 保存失败 | `examples/m01-save-fail.json` + `traces/m01-save-fail.jsonl` | `FAILED` | 保存失败 |
| M01 上限 | `examples/m01-limit.json` + `traces/m01-limit.jsonl` | `LIMIT_REACHED` | 步数上限 |
| M01 真模型 | `examples/m01-llm.json` + `traces/m01-llm.jsonl` | `NEEDS_CONFIRM` | ReAct 路径 A（冲突换时段） |
| M03 真模型 | `examples/m03-llm.json` + `traces/m03-llm.jsonl` | `NEEDS_CONFIRM` | ReAct 路径 B（warn 先确认） |

两条 ReAct 路径的差异在第 4 步：M01 观察到冲突后选 `reschedule`，M03 观察到 warn 后选 `ask`（而非直接保存）。
两次运行 `decision_degraded=false`，即没有回退到确定性策略。

## 3. 实际结果

| 断言 | 结果 |
|---|---|
| 冲突时建议 19:00 且标待确认 | 通过（`m01-conflict`） |
| 重复提交只有一份草稿、revision 仍为 1 | 通过（`m01-duplicate`） |
| 缺字段不保存、不臆测 | 通过（`m02-missing-info`，`NOT_SAVED`） |
| block 不生成草稿 | 通过（`m04-blocked`，`NOT_SAVED`） |
| 保存失败明确返回且磁盘无残留 | 通过（`m01-save-fail`，`FAILED`） |
| 无冲突素材可 `DONE` 且无待确认 | 通过（`m05-normal`） |
| 上限返回明确未完成态 | 通过（`m01-limit`，`LIMIT_REACHED`） |
| 全程无"已发布"表述 | 通过（代码中不存在发布客户端） |

## 4. 没验证的部分

| 项 | 说明 |
|---|---|
| 并发写同一草稿库 | 单进程设计前提，未加锁、未测 |
| 断电/强制中断 | 无 `fsync`，最后一次写可能丢 |
| 跨时区数据 | 只校验"口径一致"，未做换算；混用会明确报错而非静默算错 |
| 真模型在故障场景的行为 | 保存失败、达上限只在确定性策略下验证过 |
| 模型对抗性输入 | 标题注入已做单行化与截断，但未验证模型在强诱导下一定不被带偏 |
| 跨进程 CLI 幂等 | 测试内通过"重新加载磁盘上的 store"模拟，未起两个进程 |
| 大规模数据 | 5 个素材、4 条规则、1 条已有排期，未做性能测试 |

## 5. 已知限制与为什么不影响结论

| 限制 | 影响 | 为什么现在可接受 |
|---|---|---|
| 单进程、无并发锁 | 两进程同时写会丢更新 | 当前是 CLI 单次运行；并发属后续工程化 |
| 无 `fsync` | 极端断电可能丢最后一次写 | 草稿可从输入重建，不是不可恢复数据 |
| 业务失败退出码为 0 | CI 无法用退出码判断 | 结论已写入 `status` 字段；`--json` 可解析 |
| 确认无回传接口 | 只能产 `PENDING`，无法在循环内等用户 | 单次 CLI 运行无法真等人；确认边界仍可见、可测 |
| 替代时段只按整点步长、仅当日 | 可能给出不理想但可用的时段 | 题目只要求"无冲突、可确认"，不要求最优 |
| 门控较窄 | 部分步骤模型无选择余地 | 题目允许"预设流程作骨架"，分岔点已由模型决定 |
| prompt 注入仅缓解 | 理论上可影响动作选择 | 模型不能输出参数、不能越权；最坏是选错动作，门控仍兜底 |
