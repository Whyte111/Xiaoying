"""CLI 入口：python -m src.cli --material M01 [--provider llm]"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from datetime import datetime
from pathlib import Path

from .agent.llm import LLMDecider
from .agent.loop import Limits, run
from .agent.mock import RuleBasedMock
from .agent.policy import decide as policy_decide
from .config import get_settings
from .errors import AppError
from .tools.toolkit import Toolbox

ROOT = Path(__file__).resolve().parent.parent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.cli",
        description="素材审核与发布排期 Agent（只存草稿，不发布）",
    )
    parser.add_argument("--material", required=True, help="素材 ID，如 M01")
    parser.add_argument("--data-dir", default=str(ROOT / "data"))
    parser.add_argument("--store", default=str(ROOT / "data" / "drafts.json"))
    parser.add_argument("--trace", default=None, help="trace 输出路径；已存在时自动加时间戳，不覆盖旧证据")
    parser.add_argument("--max-steps", type=int, default=8)
    parser.add_argument("--fail-save", action="store_true", help="注入一次保存失败，用于验证失败语义")
    parser.add_argument("--provider", choices=["policy", "mock", "llm"], default="policy",
                        help="决策源：policy 确定性策略 / mock 离线模拟 / llm 真实模型")
    parser.add_argument("--model", default=None, help="覆盖 LLM_MODEL")
    parser.add_argument("--base-url", default=None, help="覆盖 LLM_BASE_URL")
    parser.add_argument("--json", action="store_true", help="输出完整 JSON")
    parser.add_argument("--debug", action="store_true", help="出错时打印完整堆栈")
    return parser


def unique_trace_path(path: str) -> str:
    """已有同名 trace 时追加时间戳：证据只增不改。"""
    candidate = Path(path)
    if not candidate.exists():
        return str(candidate)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return str(candidate.with_name("%s-%s%s" % (candidate.stem, stamp, candidate.suffix)))


def build_decider(args):
    if args.provider == "policy":
        return policy_decide, "policy（确定性策略，非模型）"
    if args.provider == "mock":
        return RuleBasedMock(), "mock（规则模拟模型，仅证流程）"
    settings = get_settings()
    model = args.model or settings["model"]
    base_url = args.base_url or settings["base_url"]
    if not settings["api_key"]:
        print("[warn] 未配置 LLM_API_KEY，将回退确定性策略；请参考 .env.example", file=sys.stderr)
    return LLMDecider(api_key=settings["api_key"], base_url=base_url, model=model), "llm(%s)" % model


def _execute(args) -> int:
    if args.max_steps < 1:
        print("[error] --max-steps 至少为 1", file=sys.stderr)
        return 2

    toolbox = Toolbox.load(args.data_dir, args.store)
    toolbox.store.fail_next = args.fail_save
    trace = args.trace or str(ROOT / "examples" / "traces" / ("%s-%s.jsonl" % (args.material, args.provider)))
    trace = unique_trace_path(trace)
    decider, source_label = build_decider(args)

    result = run(
        material_id=args.material,
        toolbox=toolbox,
        decide=decider,
        limits=Limits(max_steps=args.max_steps, max_tool_calls=args.max_steps),
        trace_path=trace,
    )

    degraded = any(
        ("回退确定性策略" in step.rationale) or ("非法动作" in step.rationale)
        for step in result.steps
    )

    if args.json:
        payload = result.to_dict()
        payload["decision_source"] = source_label
        payload["decision_degraded"] = degraded
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    item = result.item
    print("素材        : %s" % item.material_id)
    print("决策源      : %s" % source_label)
    print("运行状态    : %s" % result.status.value)
    print("审核状态    : %s" % item.audit_status.value)
    print("风险原因    : %s" % ("；".join(item.risk_reasons) or "无"))
    print("建议时段    : %s" % (item.suggested_slot.label() if item.suggested_slot else "无"))
    print("待确认项    : %s" % ("；".join(p.question for p in item.pending_items) or "无"))
    print("草稿状态    : %s" % item.draft_status.value)
    print("结论        : %s" % item.message)
    print("trace       : %s" % result.trace_path)
    print("-" * 60)
    for step in result.steps:
        print("%d. %-10s tool=%-14s %s" % (step.step, step.action, step.tool or "-", step.result_summary))
    if degraded:
        print("[warn] 本次运行出现过决策回退，不能作为“模型决策”的证据")
    return 0


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return _execute(args)
    except AppError as exc:
        print("[error] %s" % exc, file=sys.stderr)
        if args.debug:
            traceback.print_exc()
        return 2
    except KeyboardInterrupt:
        print("[error] 已中断", file=sys.stderr)
        return 130
    except Exception as exc:  # 兜底：不把堆栈甩给使用者
        print("[error] 未预期的内部错误：%s: %s" % (type(exc).__name__, exc), file=sys.stderr)
        if args.debug:
            traceback.print_exc()
        return 3


if __name__ == "__main__":
    sys.exit(main())
