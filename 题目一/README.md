# 素材审核与发布排期 Agent（题目一）

输入素材与已有排期，输出**审核状态 / 风险原因 / 建议时段 / 待确认项 / 草稿保存状态**。
只写草稿，不接真实发布；改动用户的硬性时段必须标为待确认。

## 1. 环境

- Python **3.9+**
- **无第三方依赖**（仅标准库；`requirements.txt` 为空壳，保留以符合交付结构）
- 真模型为可选项，不配置也能完整运行（走确定性策略或离线 Mock）

```bash
cd 题目一            # 仓库根目录下的项目目录，之后所有命令都在这里执行
```

## 2. 30 秒验证

```bash
python -m unittest discover -s tests -t . -v        # 52 项，全绿
python -m src.cli --material M01                    # 冲突：建议 19:00 并标待确认
python -m src.cli --material M05                    # 正常：DONE，无待确认
```

第 2 条输出应为 `NEEDS_CONFIRM` / `SAVED_CREATED` / 建议时段 `2026-11-10 19:00-20:00`，
第 3 条应为 `DONE` / `SAVED_CREATED` / 无待确认。

## 3. 目录

```
src/domain/    数据结构、规则、冲突判定、动作门控（纯函数，无 IO）
src/tools/     4 个业务工具：读素材 / 规则检查 / 时段查询 / 保存草稿
src/store/     草稿库：单文件 JSON、原子写、幂等 upsert、失败回滚
src/agent/     决策层：循环、动作门控、确定性策略、离线 Mock、LLM 适配
src/cli.py     唯一入口
tests/         52 项测试（unittest，无依赖）
data/          合成素材、规则、已有排期
examples/      10 组输入/实际输出/Trace，见 examples/README.md
docs/          需求、设计、评估、AI 使用、复盘
```

## 4. 命令

| 目的 | 命令 |
|---|---|
| 正常排期 | `python -m src.cli --material M05` |
| 时段冲突 | `python -m src.cli --material M01` |
| 重复提交 | 连跑两次 `python -m src.cli --material M01`，第二次为 `SAVED_IDEMPOTENT` |
| 信息缺失 | `python -m src.cli --material M02` |
| 规则风险 | `python -m src.cli --material M03` |
| 规则阻断 | `python -m src.cli --material M04` |
| 保存失败 | `python -m src.cli --material M01 --fail-save --store data/fail.json` |
| 触达上限 | `python -m src.cli --material M01 --max-steps 3 --store data/limit.json` |
| 离线 Mock | `python -m src.cli --material M03 --provider mock` |
| 真模型 | `python -m src.cli --material M01 --provider llm` |
| JSON 输出 | 追加 `--json` |
| 指定 trace | `--trace examples/traces/x.jsonl`（同名已存在时自动加时间戳，不覆盖） |

其余参数：`--data-dir`、`--store`、`--model`、`--base-url`、`--debug`。

## 5. 输出字段

| 字段 | 取值 |
|---|---|
| 审核状态 | `PASS` / `WARN` / `NEED_INFO` / `BLOCKED` |
| 风险原因 | 命中的规则说明列表 |
| 建议时段 | 无冲突时为请求时段；有冲突时为替代时段 |
| 待确认项 | 改时段、warn 风险、信息缺失等 |
| 草稿保存状态 | `SAVED_CREATED` / `SAVED_IDEMPOTENT` / `SAVED_UPDATED` / `FAILED` / `NOT_SAVED` |
| 运行状态 | `DONE` / `NEEDS_CONFIRM` / `NEEDS_INFO` / `BLOCKED` / `FAILED` / `TOOL_FAILED` / `INCOMPLETE` / `LIMIT_REACHED` |

## 6. 接入真模型（可选）

```bash
cp .env.example .env     # 填 LLM_API_KEY / LLM_BASE_URL / LLM_MODEL
python scripts/check_llm.py           # 只打印元信息，不打印密钥；HTTP 200 即可
python -m src.cli --material M01 --provider llm
```

- 未配置 key 时自动回退确定性策略，并在输出末尾打印 `[warn] 决策回退`。
- 兼容任何 OpenAI 风格的 `/chat/completions` 端点（DeepSeek、通义、OpenAI 等）。
- 密钥只从环境读取，不写入 trace、不入库（`.env` 已在 `.gitignore`）。

## 7. 评审入口

- 行为证据：`examples/README.md`（10 组，含 2 条真模型决策轨迹）
- 测试与未验证项：`docs/03-evaluation.md`
- 设计取舍：`docs/02-solution.md`
- 风险与限制：`docs/03-evaluation.md` 第 4 节
