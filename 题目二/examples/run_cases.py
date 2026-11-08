"""生成 examples/ 下的四组运行记录。

用法（仓库根目录）：
    python examples/run_cases.py

每组用例生成：input.json / output.json / trace.json / state.json / plan-*.json
案例目录会被整体重建，请勿手改后再跑。
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.decision import MockPolicy  # noqa: E402
from src.engine import Engine  # noqa: E402
from src.store import load_scenario  # noqa: E402
from src.tools import Toolbox  # noqa: E402
from src.types import TaskInput, ToolResult  # noqa: E402


class _PermanentFailToolbox(Toolbox):
    """让某个工具永久失败，用于演示续跑。"""

    def __init__(self, scenario, out_dir, tool: str) -> None:
        super().__init__(scenario, out_dir)
        self._dead_tool = tool

    def call(self, name, args) -> ToolResult:
        if name == self._dead_tool:
            fee = self.scenario["fee_table"].get(name, {"cost": 0.0})
            return ToolResult(
                ok=False, tool=name, args=args,
                error_code="BROKEN", error_msg="注入的永久故障（示例用）",
                cost=float(fee.get("cost", 0.0)),
            )
        return super().call(name, args)

CASES = [
    {
        "name": "case1-normal",
        "topic": "秋季校园活动",
        "budget": 10.0,
        "deadline": "2026-11-20",
        "fail": [],
        "runs": 1,
        "desc": "正常完成：方案 A 估算 12.50 超预算，改选方案 B 后保存",
    },
    {
        "name": "case2-budget-gap",
        "topic": "秋季校园活动",
        "budget": 5.0,
        "deadline": "2026-11-20",
        "fail": [],
        "runs": 1,
        "desc": "预算超限：两个方案都超预算，返回未完成与缺口，不保存",
    },
    {
        "name": "case3-tool-failure",
        "topic": "秋季校园活动",
        "budget": 10.0,
        "deadline": "2026-11-20",
        "fail": ["generate_script"],
        "runs": 1,
        "desc": "工具失败：脚本生成失败一次，降级为单素材重试后完成",
    },
    {
        "name": "case4-repeat",
        "topic": "秋季校园活动",
        "budget": 10.0,
        "deadline": "2026-11-20",
        "fail": [],
        "runs": 2,
        "desc": "重复执行：第二次命中已完成记录，复用结果且不重复保存",
    },
    {
        "name": "case5-missing-deadline",
        "topic": "秋季校园活动",
        "budget": 10.0,
        "deadline": None,
        "fail": [],
        "runs": 1,
        "desc": "输入不足：缺截止时间，直接返回未完成，不调用任何工具",
    },
    {
        "name": "case8-resume",
        "topic": "校园安全宣传",
        "budget": 10.0,
        "deadline": "2026-11-25",
        "fail": ["save_plan"],
        "runs": 2,
        "permanent_fail_first": True,
        "desc": "失败续跑：第 1 次保存永久失败，第 2 次继承前 3 步结果，只补跑保存",
    },
]


def _guard(path: Path) -> None:
    """只允许重建 examples/ 下的目录。"""
    if path.parent.name != "examples":
        raise RuntimeError(f"拒绝操作非 examples 目录: {path}")


def run_case(spec: dict) -> None:
    case_dir = ROOT / "examples" / spec["name"]
    _guard(case_dir)
    if case_dir.exists():
        shutil.rmtree(case_dir)
    case_dir.mkdir(parents=True)

    scenario = load_scenario()
    results = []
    for i, _ in enumerate(range(spec["runs"])):
        if spec.get("permanent_fail_first") and i == 0:
            toolbox = _PermanentFailToolbox(scenario, case_dir, spec["fail"][0])
        else:
            toolbox = Toolbox(scenario, case_dir)
            toolbox.fail_tools = set(spec["fail"]) if i == 0 else set()
        engine = Engine(
            scenario, MockPolicy(scenario), toolbox=toolbox, out_dir=case_dir
        )
        results.append(
            engine.run(
                TaskInput(
                    topic=spec["topic"], budget=spec["budget"], deadline=spec["deadline"]
                )
            )
        )
        if spec["runs"] > 1:
            shutil.copy(case_dir / "trace.json", case_dir / f"trace-run{i + 1}.json")

    (case_dir / "input.json").write_text(
        json.dumps(
            {
                "topic": spec["topic"],
                "budget": spec["budget"],
                "deadline": spec["deadline"],
                "injected_failure": spec["fail"],
                "description": spec["desc"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (case_dir / "output.json").write_text(
        json.dumps(results[-1].to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if len(results) > 1:
        (case_dir / "output-first-run.json").write_text(
            json.dumps(results[0].to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )

    print(f"[{spec['name']}] {spec['desc']}")
    for i, r in enumerate(results, 1):
        print(
            f"  run{i}: status={r.status} cost={r.total_cost} "
            f"calls={r.tool_calls} reused={r.reused}"
        )


def main() -> int:
    for spec in CASES:
        run_case(spec)
    print("\n完成，产物位于 examples/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
