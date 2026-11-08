# 短视频脚本 Agent（题目四）

一个可运行的脚本生成 Agent：模型在循环里**自己选动作**（检索素材 / 写稿 / 估时长 / 压缩 / 查规则 / 结束），
系统执行确定性工具并把结果回灌给它，直到交付或明确失败。

- 语言：Python **3.9+**，**只用标准库**，不需要 `pip install`
- 形态：CLI
- 模型：DeepSeek（OpenAI 兼容接口），无网络/无 key 时可切换到离线 Mock
- 测试：**89 项**，全绿，`python -m unittest`

---

## 1. 三分钟跑通

```bash
# 1) 进入项目根目录（必须，CLI 以 src 包方式运行）
cd 题目四

# 2) 不需要安装任何依赖；确认解释器可用
python --version

# 3) 不配任何密钥，先跑离线 Mock（验证流程）
python -m src.cli run --input examples/inputs/01_normal.json --model mock

# 4) 跑全部测试
python -m unittest discover -s tests -t .
```

第 3 步预期输出 `"status": "completed"`，并在 `examples/runs/` 生成
`01_normal.mock.trace.jsonl`（逐步骤运行记录）和 `01_normal.mock.result.json`（最终结果）。

## 2. 接真实模型

```bash
# 1) 复制模板，填入你自己的 key
cp .env.example .env

# 2) 编辑 .env（三个变量）
#   LLM_API_KEY=<你的密钥，不要提交> 
#   LLM_BASE_URL=https://api.deepseek.com
#   LLM_MODEL=deepseek-chat

# 3) 用真模型跑
python -m src.cli run --input examples/inputs/01_normal.json --model real
```

> `.env` 已在 `.gitignore` 中，不会被提交。
> 代码只在运行时读取它，密钥不会进入日志、结果文件或 trace。

## 3. 命令说明

```
python -m src.cli run --input <json> [--model auto|real|mock] [--out <dir>] [--max-steps N] [--debug]
```

| 参数 | 说明 |
|---|---|
| `--model real` | 强制真实模型；无密钥时直接报错（不静默降级） |
| `--model mock` | 离线确定性 Mock，只验证流程，**不证明**模型决策能力 |
| `--model auto`（默认） | 有密钥走真模型，否则降级 Mock，并在 trace 里把模型名记为 `mock(offline-fallback)` |
| `--out` | 输出目录，默认 `examples/runs` |
| `--max-steps` | 步数上限，默认 16，**强制夹取到 [1,100]** |
| `--debug` | 异常时打印完整堆栈 |

**退出码**（便于脚本判定）：

| 码 | 含义 |
|---|---|
| 0 | `completed` |
| 1 | 未完成（`incomplete` / `needs_input` / `failed`） |
| 2 | 输入文件或字段非法 |
| 3 | 运行期错误（模型不可用、trace 无法落盘等） |

## 4. 输入格式

```json
{
  "topic": "智能剪辑器",
  "audience": "个人短视频创作者",
  "platform": "douyin",
  "target_duration_s": 30,
  "style": "简洁口播",
  "material_ids": ["P01"]
}
```

- 必填：`topic` / `platform` / `target_duration_s`（> 0 且 ≤ 600）
- `material_ids` 可选；为空时 Agent 会自己检索
- 字段类型错误会返回退出码 2，不会崩溃

## 5. 验证材料在哪

| 内容 | 位置 |
|---|---|
| 6 个场景输入 | `examples/inputs/` |
| 逐步骤运行记录（含决策依据） | `examples/runs/*.trace.jsonl` |
| 最终结果（可直接核对） | `examples/runs/*.result.json` |
| 测试代码 | `tests/` |

实测结果（`examples/runs/` 中的真实产物）：

| 场景 | 状态 | 关键证据 |
|---|---|---|
| `01_normal` | completed，18.4s | 正常交付 |
| `02_overrun` | completed，7.0s | 先 8.8s 超时 → 压缩 → 7.0s 通过 |
| `03_unsatisfiable` | incomplete / `UNSATISFIABLE` | 目标 0.9s 物理不可行，4 步内终止 |
| `04_missing_input` | needs_input / `MISSING_INPUT` | 0 步，进循环前拦下 |
| `05_no_material` | needs_input / `SEARCH_EMPTY` | 检索空 → 请求补充 |
| `06_tool_failure` | completed，5.6s | 素材 ID 失效报错 → 换关键词恢复 |

## 6. 目录结构

```
.
├── README.md
├── requirements.txt          # 无第三方依赖
├── .env.example              # 三个变量名
├── data/                     # 合成素材与品牌规则（只读）
├── src/
│   ├── cli.py                # 唯一入口
│   ├── config.py             # 数据加载
│   ├── domain/               # 数据结构与错误码
│   ├── agent/                # 循环、护栏、分发、提示词
│   ├── llm/                  # 真实模型与 Mock（同一接口）
│   ├── tools/                # search / duration / rules / compress
│   └── observability/        # trace 写入与脱敏
├── tests/                    # 89 项，unit / loop / scenarios / cli
├── examples/inputs/          # 6 个场景
├── examples/runs/            # 实际产物
└── docs/                     # 需求、设计、测试、AI 使用、回顾
```

## 7. 文档索引

| 文档 | 内容 |
|---|---|
| `docs/01-requirements.md` | 用户、目标、范围、成功标准 |
| `docs/02-solution.md` | 为什么这样设计、核心结构 |
| `docs/03-evaluation.md` | 验证了什么、**没验证什么** |
| `docs/04-ai-usage.md` | AI 用在哪、人改了什么 |
| `docs/05-retrospective.md` | **风险与限制**、最难的问题、下一步 |

过程文档（非交付必需）：`问题定义.md`、`方案对比.md`、`系统骨架.md`。
