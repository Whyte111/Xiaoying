# 企业知识问答 Agent

依据公司制度文档回答员工问题。每次回答固定输出四件套：**回答 / 引用原文 / 资料缺口 / 任务状态**。
检索与追问由模型决策，循环为「模型选动作 → 执行工具 → 观察结果 → 模型决定下一步」。

- 环境：Python **3.9+**，运行时**无第三方依赖**（可选装 `jieba` 提升分词）
- 模型：DeepSeek（OpenAI 兼容端点），离线可完全脱离模型运行

## 1. 30 秒跑通（离线，不需要密钥）

在项目根目录执行：

```bash
python -m src.cli ask "出差住宿标准是多少？" --offline --script tests/fixtures/script_happy.json
```

预期输出：

```
状态: ANSWERED
回答: 一线城市住宿标准为每人每晚 600 元，二线城市为每人每晚 400 元，超出标准部分由个人承担。
引用:
  - D01 v2024-03-01 §2. 住宿标准
    "一线城市住宿标准为每人每晚 600 元，二线城市为每人每晚 400 元，超出标准部分由个人承担。"
```

> 引用行里每个字都来自 `data/` 原文，可用编辑器搜索核对。

## 2. 跑测试

```bash
python -m unittest discover -s tests -t .
```

26 条用例，全离线、无网络、约 1 秒。覆盖主链路、冲突、资料不足、工具失败、JSON 重试、步数上限。

## 3. 接真实模型

```bash
cp .env.example .env      # Windows: copy .env.example .env
# 编辑 .env，填入 LLM_API_KEY
python -m src.cli ask "出差住宿标准是多少？"
```

`.env` 已在 `.gitignore` 中；仓库里只有占位符 `.env.example`。

## 4. 命令行参数

```bash
python -m src.cli ask "<问题1>" ["<问题2>" ...] [选项]
```

| 选项 | 说明 |
|---|---|
| 多个问题 | 按顺序作为**多轮对话**，后续问题可依赖上文 |
| `--offline` | 离线模式，用脚本回放代替模型 |
| `--script <path>` | 离线脚本（JSON 数组，每个元素是一次决策） |
| `--trace-out <path>` | 输出执行轨迹，仅允许写入 `runs/` 或 `examples/` |
| `--steps <N>` | 覆盖单轮最大步数（默认 6） |
| `--fault search --fail-times N` | 故障注入：让检索前 N 次失败，用于演示失败路径 |

> Windows 下若中文参数乱码，先执行 `chcp 65001` 或设置 `$env:PYTHONUTF8=1`。

## 5. 目录结构

```
data/            3 篇合成制度文档（含一对故意冲突的报销时限）
src/             实现代码
  cli.py         入口
  agent.py       ReAct 循环、步数预算、冲突门禁
  tools.py       search / read_doc / ask_user / finish
  retrieval.py   jieba + BM25
  conflict.py    冲突检测（确定性规则）
  answer.py      引用校验与状态裁决
  llm.py         DeepSeek 客户端 + 离线 Fake
  session.py     多轮上下文
  trace.py       轨迹落盘
tests/           26 条 unittest + 离线脚本 fixtures
examples/        4 组「输入 / 实际输出 / 轨迹」
docs/            需求、设计、评估、AI 使用、复盘
```

## 6. 示例（`examples/`）

| 目录 | 场景 | 状态 | 可离线复现 |
|---|---|---|---|
| `01-answer` | 正常问答 | `ANSWERED` | ✅ |
| `02-conflict` | 两份制度冲突 | `NEEDS_CLARIFICATION` | ✅ |
| `03-insufficient` | 资料外问题 | `INSUFFICIENT` | ✅ |
| `04-tool-failure` | 检索连续失败 | `TOOL_ERROR` | ✅ |
| `05-multiturn` | 上下文追问（两轮） | `ANSWERED` ×2 | ✅ |
| `06-paraphrase` | 口语化改写提问 | `ANSWERED` | ❌ 在线实跑记录 |

每组含 `input.txt`（问题+可复制命令）、`output.txt`（真实输出）、`trace.jsonl`（逐步轨迹）。

## 7. 已知限制（摘要）

- 冲突检测与引用相关性都是**词面启发式**，不是语义推理；大改文档措辞可能漏判。
- 会话状态仅存内存，进程结束即丢失，不支持跨命令续聊。
- 在线效果未做多次重复采样，单次结果不代表稳定性。

完整说明见 [docs/03-evaluation.md](docs/03-evaluation.md)。

## 8. 文档

- [docs/01-requirements.md](docs/01-requirements.md) — 用户、目标、范围、成功标准
- [docs/02-solution.md](docs/02-solution.md) — 设计取舍与核心结构
- [docs/03-evaluation.md](docs/03-evaluation.md) — 验证了什么、没验证什么、风险
- [docs/04-ai-usage.md](docs/04-ai-usage.md) — AI 辅助部分与人工修改
- [docs/05-retrospective.md](docs/05-retrospective.md) — 复盘

## 9. 过程记录（可选阅读）

根目录的 `问题定义.md`、`方案对比.md`、`系统骨架.md`、`实现进度.md` 是开发过程中的文档，不属于交付正文，评审无需阅读。

## 10. 提交信息

- 题号：三
- 仓库：待填
- commit SHA：待填
