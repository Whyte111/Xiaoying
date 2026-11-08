# 内容任务规划 Agent（题目二）

一个能跑通 ReAct 闭环的内容活动规划 Agent：模型根据工具返回结果选择下一步，在预算约束下完成「检索素材 → 生成脚本 → 成本估算 → 保存方案」，失败时保留已成功步骤并给出未完成原因。

## 环境

- Python 3.9+（开发环境 3.9.2）
- **零第三方依赖**，标准库即可运行
- 可选：真实模型需要 `.env` 里的 DeepSeek API Key，不配也能跑（默认离线 Mock）

## 快速开始

在仓库根目录执行（必须是根目录，`src` 是包）：

```bash
python -m src.cli --topic "秋季校园活动" --budget 10 --deadline 2026-11-20
```

预期输出：

```
状态: completed
主题: 秋季校园活动   预算: 10.0   总花费: 8.6（工具费 0.6）
轮次: 6   工具调用: 5   决策来源: mock
步骤:
  - s1 检索素材: succeeded
  - s2 生成脚本: succeeded
  - s3 成本估算: succeeded
  - s4 保存方案: succeeded
时间核算: 未启用（题目未提供可用工时，仅校验截止时间格式）
已保存: out/plan-fc8dfe2f.json
Trace: out/trace.json
```

> 首次运行才会有上面的输出；重复执行同一输入会命中幂等记录，显示「复用已有结果、未调用工具」。`out/` 是运行产物，已在 `.gitignore` 中。

### 退出码

| 码 | 含义 |
|---|---|
| 0 | 任务完成，方案已保存 |
| 1 | 未完成（预算不足、工具失败、触顶等），原因见输出 |
| 2 | 数据文件缺失或格式错误 |

### 常用参数

```bash
python -m src.cli --topic "..." --budget 10 --deadline 2026-11-20 --json   # 结构化输出
python -m src.cli --topic "..." --budget 10 --deadline 2026-11-20 --out-dir out/demo
```

`--deadline` 为必填，接受 `2026-11-20` 或 `2026-11-20T18:00`。

## 运行真实模型

```bash
cp .env.example .env      # 填入 LLM_API_KEY
python -m src.cli --provider llm --topic "秋季校园活动" --budget 10 --deadline 2026-11-20
```

不加 `--provider llm` 时只用离线规则决策器，不发起任何网络请求。

## 验证

```bash
# 1. 全量测试（41 条，约 1 秒）
python -m unittest discover -s tests -v

# 2. 重建全部示例运行记录
python examples/run_cases.py

# 3. 单点验证：预算边界（8.61 应完成、8.59 应失败）
python -m src.cli --topic "秋季" --budget 8.61 --deadline 2026-11-20

# 4. 故障注入（Windows PowerShell），验证降级与续跑
$env:FAIL_STEP='s2'; python -m src.cli --topic "秋季校园活动" --budget 10 --deadline 2026-11-20
```

## 目录

```
src/            代码（cli / engine / decision / guards / tools / state / llm）
data/           场景数据：素材、费用表、备选方案
tests/          自动化测试
examples/       实际运行记录（input / output / trace / 产物）
docs/           需求、设计、评估、AI 使用、复盘
```

## 产物说明

| 文件 | 内容 |
|---|---|
| `out/result.json` | 结构化结果：计划、步骤状态、工具结果、费用、产物或未完成原因 |
| `out/trace.json` | 决策轨迹：动作、工具参数、返回摘要、决策依据、来源（llm/mock/fallback） |
| `out/state.json` | 输入指纹 → 已完成结果 / 未完成快照（幂等与续跑） |
| `out/plan-*.json` | 保存下来的方案 |

## 文档

- [需求与验收](docs/01-requirements.md)
- [设计说明](docs/02-solution.md)
- [测试与评估](docs/03-evaluation.md)
- [AI 使用说明](docs/04-ai-usage.md)
- [复盘](docs/05-retrospective.md)

## 提交信息

- 题号：题目二
- GitHub URL：待填
- commit SHA：待填
