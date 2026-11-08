"""命令行入口。

唯一对外命令面：
    python -m src.cli run --input <json> [--model auto|real|mock] [--out <dir>] [--max-steps N]

退出码约定（便于脚本判定，避免“崩了也算跑完”）：
    0 completed
    1 未完成（incomplete / needs_input / failed）
    2 输入文件或字段非法
    3 运行期错误（模型不可用、trace 无法落盘等）
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .agent.loop import Loop
from .agent.policy import Policy
from .config import ROOT, load_ctx
from .domain.schemas import TaskInput
from .llm.mock_client import MockModel
from .observability.trace import TraceWriter

# 步数上限的绝对天花板：防止 --max-steps 传天文数字绕过“必须有上限”的约束
MAX_STEPS_CAP = 100
DEFAULT_MAX_STEPS = 16


def clamp_max_steps(value) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return DEFAULT_MAX_STEPS
    return max(1, min(number, MAX_STEPS_CAP))


def _pick_model(name: str):
    if name == "mock":
        return MockModel()
    if name == "real":
        from .llm.deepseek_client import DeepSeekModel

        return DeepSeekModel()

    # auto：有密钥走真模型；降级时**在名字里留痕**，避免产出冒充真模型证据
    has_key = bool(
        os.environ.get("LLM_API_KEY") or os.environ.get("DEEPSEEK_API_KEY")
    ) or (ROOT / ".env").exists()
    if has_key:
        try:
            from .llm.deepseek_client import DeepSeekModel

            return DeepSeekModel()
        except Exception as exc:
            print(
                "[warn] 真模型不可用（{e}），本次降级为离线 Mock。"
                "该次 trace 不能作为真实模型决策证据。".format(e=exc),
                file=sys.stderr,
            )
    fallback = MockModel()
    fallback.name = "mock(offline-fallback)"
    return fallback


def cmd_run(args: argparse.Namespace) -> int:
    input_path = Path(args.input)
    if not input_path.exists():
        print("[error] 输入文件不存在：{p}".format(p=input_path), file=sys.stderr)
        return 2

    try:
        # utf-8-sig：兼容 Windows 记事本/PowerShell 生成的带 BOM 文件
        raw = json.loads(input_path.read_text(encoding="utf-8-sig"))
        task = TaskInput.from_dict(raw)
    except json.JSONDecodeError as exc:
        print("[error] 输入不是合法 JSON：{e}".format(e=exc), file=sys.stderr)
        return 2
    except (OSError, UnicodeDecodeError) as exc:
        print("[error] 无法读取输入文件：{e}".format(e=exc), file=sys.stderr)
        return 2
    except ValueError as exc:
        print("[error] 输入字段不合法：{e}".format(e=exc), file=sys.stderr)
        return 2

    try:
        model = _pick_model(args.model)
    except Exception as exc:
        print("[error] 模型初始化失败：{e}".format(e=exc), file=sys.stderr)
        return 3

    out_dir = Path(args.out)
    stem = "{name}.{model}".format(
        name=input_path.stem, model=getattr(model, "name", "unknown").replace("(", "_").replace(")", "")
    )
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        trace = TraceWriter(out_dir / (stem + ".trace.jsonl"))
    except OSError as exc:
        print("[error] 无法创建输出目录或 trace 文件：{e}".format(e=exc), file=sys.stderr)
        return 3

    loop = Loop(
        model=model,
        ctx=load_ctx(),
        policy=Policy(max_steps=clamp_max_steps(args.max_steps)),
        trace=trace,
    )

    try:
        result = loop.run(task)
    except Exception as exc:  # 兜底：任何未预期异常都不以裸 traceback 结束
        if getattr(args, "debug", False):
            raise
        print(
            "[error] 运行异常（{t}）：{e}；如需完整堆栈请加 --debug".format(
                t=type(exc).__name__, e=exc
            ),
            file=sys.stderr,
        )
        return 3

    result_path = out_dir / (stem + ".result.json")
    try:
        result_path.write_text(
            json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError as exc:
        print("[error] 无法写入结果文件：{e}".format(e=exc), file=sys.stderr)
        return 3

    print(
        json.dumps(
            {
                "status": result.status,
                "error_code": result.error_code,
                "reason": result.reason,
                "steps_used": result.steps_used,
                "tool_calls_used": result.tool_calls_used,
                "estimated_total_s": (result.script or {}).get("estimated_total_s"),
                "trace": str(trace.path),
                "result_file": str(result_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if result.status == "completed" else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="src.cli", description="短视频脚本 Agent（最小闭环）")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="运行一个输入")
    run.add_argument("--input", required=True, help="任务输入 JSON 路径")
    run.add_argument(
        "--model",
        default="auto",
        choices=["auto", "real", "mock"],
        help="auto=有密钥走真模型否则 Mock",
    )
    run.add_argument("--out", default=str(ROOT / "examples" / "runs"), help="输出目录")
    run.add_argument("--max-steps", type=int, default=DEFAULT_MAX_STEPS, help="步数上限（最大 100）")
    run.add_argument("--debug", action="store_true", help="异常时输出完整堆栈")
    run.set_defaults(func=cmd_run)
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
