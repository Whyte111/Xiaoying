# 证据索引

每组用例包含：输入（`data/` 中被引用的素材与规则）、实际输出（`*.json`）、执行 Trace（`traces/*.jsonl`）。
全部由当前代码实跑生成，未手写。

| 用例 | 输入 | 实际输出 | Trace | 运行状态 |
|---|---|---|---|---|
| 正常排期 | M05 | `m05-normal.json` | `traces/m05-normal.jsonl` | `DONE` |
| 时段冲突 | M01 | `m01-conflict.json` | `traces/m01-conflict.jsonl` | `NEEDS_CONFIRM` |
| 重复提交 | M01（同 store 连跑） | `m01-duplicate.json` | `traces/m01-duplicate.jsonl` | `SAVED_IDEMPOTENT` |
| 信息缺失 | M02 | `m02-missing-info.json` | `traces/m02-missing-info.jsonl` | `NEEDS_INFO` |
| 规则风险 warn | M03 | `m03-rule-warn.json` | `traces/m03-rule-warn.jsonl` | `WARN` → `NEEDS_CONFIRM` |
| 规则风险 block | M04 | `m04-blocked.json` | `traces/m04-blocked.jsonl` | `BLOCKED` |
| 保存失败 | M01 + `--fail-save` | `m01-save-fail.json` | `traces/m01-save-fail.jsonl` | `FAILED` |
| 步数上限 | M01 + `--max-steps 3` | `m01-limit.json` | `traces/m01-limit.jsonl` | `LIMIT_REACHED` |
| 真模型·冲突换时段 | M01 | `m01-llm.json` | `traces/m01-llm.jsonl` | `NEEDS_CONFIRM` |
| 真模型·warn 先确认 | M03 | `m03-llm.json` | `traces/m03-llm.jsonl` | `NEEDS_CONFIRM` |

## Trace 字段

```json
{"step": 3, "action": "query", "tool": "query_slots",
 "args": {"slot": "2026-11-10 18:00-19:00"},
 "result_summary": "时段 2026-11-10 18:00-19:00 冲突 1 处",
 "rationale": "存在冲突 → 生成替代时段"}
```

`step` 步骤号、`action` 本步动作、`tool` 调用的工具（纯决策动作为 `null`）、`args` 工具参数、
`result_summary` 返回摘要、`rationale` 决策依据（真模型时附模型给的理由）。

## 复现

以下命令均在 `题目一/` 目录下执行。

```bash
# 冲突
python -m src.cli --material M01 --store data/demo.json --trace examples/traces/m01-conflict.jsonl --json

# 重复提交（上一条之后再跑，必须用同一个 --store）
python -m src.cli --material M01 --store data/demo.json --trace examples/traces/m01-duplicate.jsonl --json

# 其余
python -m src.cli --material M05 --store data/demo.json --trace examples/traces/m05-normal.jsonl --json
python -m src.cli --material M02 --store data/demo.json --trace examples/traces/m02-missing-info.jsonl --json
python -m src.cli --material M03 --store data/demo.json --trace examples/traces/m03-rule-warn.jsonl --json
python -m src.cli --material M04 --store data/demo.json --trace examples/traces/m04-blocked.jsonl --json
python -m src.cli --material M01 --fail-save --store data/fail.json --trace examples/traces/m01-save-fail.jsonl --json
python -m src.cli --material M01 --max-steps 3 --store data/limit.json --trace examples/traces/m01-limit.jsonl --json

# 真模型（需 .env 配 key）
python -m src.cli --material M01 --provider llm --store data/llm.json --trace examples/traces/m01-llm.jsonl --json
python -m src.cli --material M03 --provider llm --store data/llm.json --trace examples/traces/m03-llm.jsonl --json
```

`--json` 输出到 stdout；保存为 `.json` 时注意用 UTF-8 无 BOM。
若 PowerShell 重定向出现 BOM，用 `--json | Out-File -Encoding utf8` 即可。
