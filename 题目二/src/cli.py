"""命令行入口：解析入参、跑主循环、输出结果。"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import List, Optional

from . import config
from .decision import MockPolicy
from .engine import Engine
from .llm import LLMDecisionPolicy
from .store import load_scenario
from .types import STATUS_COMPLETED, TaskInput


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m src.cli",
        description="内容任务规划 Agent（最小可运行闭环）",
    )
    p.add_argument("--topic", required=True, help="内容主题")
    p.add_argument("--budget", type=float, default=None, help="预算（模拟单位）")
    p.add_argument("--deadline", default=None, help="截止时间（暂未参与核算）")
    p.add_argument("--json", action="store_true", help="输出结构化 JSON")
    p.add_argument(
        "--provider", choices=["mock", "llm"], default=None,
        help="决策来源：mock（默认，离线）/ llm（需 .env 里的 LLM_API_KEY）",
    )
    p.add_argument(
        "--out-dir", default=None,
        help="结果与 trace 的输出目录，默认 out/",
    )
    return p


def _render(result) -> str:
    label = "总花费" if result.saved_artifact else "已花费"
    status_line = f"状态: {result.status}"
    if result.reused:
        status_line += "（重复执行，复用已有结果，未调用工具）"
    if result.resumed:
        status_line += "（续跑，已继承上次成功的步骤）"
    lines = [
        status_line,
        f"主题: {result.topic}   预算: {result.budget}   {label}: {result.total_cost}"
        f"（工具费 {result.tool_fee}）",
        f"轮次: {result.rounds}   工具调用: {result.tool_calls}   决策来源: {result.provider}",
        "步骤:",
    ]
    for item in result.plan:
        lines.append(f"  - {item.step_id} {item.name}: {item.status}")
    if result.plan_cost and not result.saved_artifact:
        lines.append(f"已选方案估算成本: {result.plan_cost}（未保存，不计入已花费）")
    lines.append(
        "时间核算: 已启用" if result.time_checked
        else "时间核算: 未启用（题目未提供可用工时，仅校验截止时间格式）"
    )
    if result.fallback_calls:
        lines.append(f"规则兜底: {result.fallback_calls} 轮（模型调用或解析失败）")
    for v in result.violations:
        lines.append(f"约束: {v.code} - {v.detail}")
    if result.saved_artifact:
        lines.append(f"已保存: {result.saved_artifact.get('path')}")
    if result.blocked_reason:
        lines.append(f"未完成原因: {result.blocked_reason}")
    if result.trace_path:
        lines.append(f"Trace: {result.trace_path}")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

    args = build_parser().parse_args(argv)
    try:
        scenario = load_scenario()
    except (FileNotFoundError, ValueError) as exc:
        print(f"数据错误: {exc}", file=sys.stderr)
        return 2

    provider = args.provider or os.environ.get("PROVIDER", "mock")
    if provider == "llm":
        config.load_env()
        if not os.environ.get("LLM_API_KEY"):
            print("缺少 LLM_API_KEY，请检查 .env", file=sys.stderr)
            return 2
        policy = LLMDecisionPolicy(scenario)
    else:
        policy = MockPolicy(scenario)

    engine = Engine(scenario, policy, out_dir=Path(args.out_dir) if args.out_dir else None)
    result = engine.run(TaskInput(topic=args.topic, budget=args.budget, deadline=args.deadline))

    out_dir = Path(args.out_dir) if args.out_dir else config.OUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "result.json").write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(_render(result))
    return 0 if result.status == STATUS_COMPLETED else 1


if __name__ == "__main__":
    raise SystemExit(main())
