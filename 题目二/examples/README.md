# examples —— 实际运行记录

本目录由 `python examples/run_cases.py` 生成，**不要手改**；改动代码后重新生成即可。

每组用例包含：

| 文件 | 内容 |
|---|---|
| `input.json` | 输入参数与注入的故障 |
| `output.json` | 本次运行的 `FinalResult`（结构化结果） |
| `trace.json` | 决策轨迹：动作、工具参数、返回摘要、决策依据 |
| `trace-runN.json` | 多轮用例（`case4`、`case8`）每次运行的单独轨迹 |
| `state.json` | 幂等记录（输入指纹 → 已完成结果） |
| `plan-*.json` | 保存下来的方案产物（仅完成态） |

## 复现

```bash
python examples/run_cases.py
```

单条命令等价形式（以 case1 为例）：

```bash
python -m src.cli --topic "秋季校园活动" --budget 10 --deadline 2026-11-20
```

## 用例

| 用例 | 场景 | 结果 | 覆盖验收点 |
|---|---|---|---|
| `case1-normal` | 预算 10，先估主方案 12.50 超限，改选替代方案 8.60 | completed，5 次调用 | 正常完成、预算超限、动态重规划 |
| `case2-budget-gap` | 预算 5，两个方案都超限 | incomplete，缺口 3.60，不保存 | 预算超限、未完成原因、失败不记成功 |
| `case3-tool-failure` | 注入脚本生成失败一次 | completed，6 次调用，降级为单素材后成功 | 工具失败、调整计划、保留成功结果 |
| `case4-repeat` | 同一输入执行两次 | 第二次 `reused=true`，0 次调用，同一产物 | 重复执行、幂等 |
| `case5-missing-deadline` | 缺截止时间 | incomplete，0 次调用 | 缺输入阻断、未完成原因 |
| `case8-resume` | 第 1 次保存永久失败，第 2 次重跑 | 第 2 次 `resumed=true`，只补跑保存（1 次调用） | 失败续跑、保留已成功步骤、`skipped` 状态 |
| `case6-llm` | **真实模型**，预算 10 | completed，6 轮，全程 `source=llm` | 模型自主重规划（A→B）、无降级 |
| `case7-llm-budget-gap` | **真实模型**，预算 5 | incomplete，模型自主 stop 并给出缺口 | 无可行方案时停止、不硬保存 |

前 5 组离线可复现；后 2 组需要 `.env` 里的 `LLM_API_KEY`，是真实模型的运行记录（模型输出不保证逐字可复现，但决策路径可核对）：

```bash
python -m src.cli --provider llm --topic "秋季校园活动" --budget 10 --deadline 2026-11-20 --out-dir examples/case6-llm
python -m src.cli --provider llm --topic "秋季校园活动" --budget 5  --deadline 2026-11-20 --out-dir examples/case7-llm-budget-gap
```

## 怎么核对

- **预算是否算对**：`output.json` 的 `total_cost = plan_cost + tool_fee`，`total_cost` 与 `data/scenario.json` 的费用表能对上。
- **是否真的改了动作**：`trace.json` 里连续两条 `estimate_cost`，第二条的 `reason` 写明“改选方案 B”。
- **失败步骤有没有被记成功**：`case3` 的 `tool_results` 中有一条 `ok=false`；`case2` 的 `plan` 里 `s4` 仍是 `pending`。
- **重复执行有没有重复保存**：`case4` 目录下只有一个 `plan-*.json`。
- **是不是模型在决策**：`case6-llm` / `case7-llm-budget-gap` 的 `trace.json` 每轮都有 `source` 字段，全为 `llm` 即未触发规则降级；同一 `input.json` 重跑会因指纹相同而复用旧结果，核对时先删 `state.json`。

## 已知边界

- 产物 ID 由 `topic + option` 哈希生成，**同输入必得同 ID**，所以 `case1` 与 `case3` 的 `plan-*.json` 同名。
- `deadline` 目前只校验必填与格式，**不参与可行性核算**（题目未提供可用工时），结果里 `time_checked=false` 即为证据。
- `case4` 的第二次运行只产生一条 `trace.json` 记录（复用动作），不代表重新决策。
