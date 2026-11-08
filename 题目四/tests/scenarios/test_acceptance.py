"""验收场景断言层。

分两层，分别回答两个不同问题：

Layer A（机制层）：给定确定性的模型行为，系统是否会收敛到正确的终态？
                 —— 用 ScriptedModel / MockModel 驱动，可重复、无网络。
Layer B（证据层）：examples/ 里实际产出的 trace 是否满足各场景的验收契约？
                 —— 只看快照，防止"改了代码但忘了重跑样例"。

两个都过，才说明"这个场景是被实现过的"，而不只是"曾经跑出来过一次"。
"""

import json
import unittest

from src.agent.loop import Loop
from src.agent.policy import Policy
from src.config import ROOT, load_ctx
from src.domain.errors import ErrorCode
from src.domain.schemas import ActionDecision, TaskInput
from src.llm.mock_client import MockModel
from src.observability.trace import TraceWriter

RUNS_DIR = ROOT / "examples" / "runs"


def task_of(**kw):
    base = dict(
        topic="智能剪辑器",
        audience="个人创作者",
        platform="douyin",
        target_duration_s=30,
        style="简洁口播",
    )
    base.update(kw)
    return TaskInput.from_dict(base)


class ScriptedModel:
    name = "scripted"

    def __init__(self, decisions):
        self.decisions = list(decisions)
        self.i = 0

    def decide(self, state):
        if self.i >= len(self.decisions):
            return ActionDecision("finish", {}, "脚本用尽")
        d = self.decisions[self.i]
        self.i += 1
        return d


class ExplodingModel:
    name = "exploding"

    def decide(self, state):
        raise AssertionError("模型不应被调用")


def run(decisions=None, model=None, task=None, policy=None):
    trace = TraceWriter(None)
    m = model or ScriptedModel(decisions or [])
    loop = Loop(model=m, ctx=load_ctx(), policy=policy or Policy(), trace=trace)
    return loop.run(task or task_of()), trace


def tools_of(trace):
    return [e.get("tool") for e in trace.events if e.get("event") == "tool_result"]


def error_codes(trace):
    return [
        e.get("error_code")
        for e in trace.events
        if e.get("event") == "tool_result" and e.get("error_code")
    ]


def shot(shot_id, narration, material_id, fact):
    return {
        "shot_id": shot_id,
        "narration": narration,
        "material_ids": [material_id],
        "fact_refs": [{"material_id": material_id, "fact": fact}],
    }


# ---------------------------------------------------------------- Layer A
class TestScenarioMachinery(unittest.TestCase):
    """场景 1：正常生成"""

    def test_normal_generation_completes_with_full_contract(self):
        result, trace = run(model=MockModel())
        self.assertEqual(result.status, "completed")
        for field in ("title", "shots", "estimated_total_s", "material_refs", "risks", "status"):
            self.assertIn(field, result.script)
        for tool in ("compose", "estimate", "check_rules"):
            self.assertIn(tool, tools_of(trace))

    """场景 2：缺关键信息"""

    def test_missing_key_field_stops_before_model(self):
        result, trace = run(task=task_of(platform=""), model=ExplodingModel())
        self.assertEqual(result.status, "needs_input")
        self.assertEqual(result.error_code, ErrorCode.MISSING_INPUT.value)
        self.assertEqual(result.steps_used, 0)
        self.assertIn("platform", result.reason)

    """场景 3：无素材"""

    def test_empty_material_ends_with_clarify(self):
        result, trace = run(model=MockModel(), task=task_of(topic="智能咖啡机"))
        self.assertEqual(result.status, "needs_input")
        self.assertEqual(result.error_code, ErrorCode.SEARCH_EMPTY.value)
        self.assertIn("ask_clarify", [e.get("action") for e in trace.events if e.get("event") == "decision"])

    """场景 4：工具失败"""

    def test_tool_failure_then_recovers(self):
        decisions = [
            ActionDecision("search", {"query": "", "ids": ["P99"]}, "使用用户给的素材 ID"),
            ActionDecision("rewrite_query", {"query": "智能剪辑器"}, "ID 失效，改用关键词检索"),
            ActionDecision(
                "compose",
                {
                    "title": "t",
                    "shots": [
                        shot("s1", "这款工具支持字幕。", "P01", "支持字幕"),
                        shot("s2", "还支持横竖屏切换。", "P01", "支持横竖屏切换"),
                    ],
                },
                "已有素材，提交草案",
            ),
            ActionDecision("estimate", {}, "估算时长"),
            ActionDecision("check_rules", {}, "检查规则"),
            ActionDecision("finish", {}, "通过，交付"),
        ]
        result, trace = run(decisions, task=task_of(target_duration_s=30))
        self.assertEqual(result.status, "completed")
        self.assertIn(ErrorCode.TOOL_ERROR.value, error_codes(trace), "工具失败必须被记录")
        actions = [e.get("action") for e in trace.events if e.get("event") == "decision"]
        self.assertLess(actions.index("search"), actions.index("rewrite_query"))
        self.assertLess(actions.index("rewrite_query"), actions.index("compose"))

    """场景 5：超时长"""

    def test_overrun_is_corrected_deterministically(self):
        first, t1 = run(model=MockModel())
        second, t2 = run(model=MockModel())
        self.assertEqual(first.status, "completed")
        self.assertEqual(first.script, second.script)
        self.assertIn("compress", tools_of(t1))
        self.assertIn(ErrorCode.DURATION_EXCEEDED.value, error_codes(t1))

    def test_impossible_target_fails_fast(self):
        decisions = [
            ActionDecision(
                "compose",
                {"title": "t", "shots": [shot("s1", "字" * 200, "P01", "支持字幕")]},
                "提交长草案",
            ),
            ActionDecision("estimate", {}, "估算"),
            ActionDecision("compress", {}, "压缩"),
        ]
        result, trace = run(decisions, task=task_of(target_duration_s=0.9), policy=Policy(max_steps=10))
        self.assertEqual(result.status, "incomplete")
        self.assertEqual(result.error_code, ErrorCode.UNSATISFIABLE.value)
        self.assertLessEqual(result.steps_used, 4, "不可满足必须立即收敛")


# ---------------------------------------------------------------- Layer B
SCENARIO_CONTRACTS = {
    "01_normal": {
        "allowed_status": {"completed"},
        "required_tools": ["compose", "estimate", "check_rules"],
    },
    "02_overrun": {
        "allowed_status": {"completed"},
        "required_tools": ["compress"],
        "required_errors": [ErrorCode.DURATION_EXCEEDED.value],
    },
    "03_unsatisfiable": {
        "allowed_status": {"incomplete", "needs_input"},
        "allowed_errors": {
            ErrorCode.UNSATISFIABLE.value,
            ErrorCode.MISSING_INPUT.value,
            ErrorCode.DURATION_EXCEEDED.value,  # 失败前必然先超时一次
        },
        "max_steps": 6,
    },
    "04_missing_input": {
        "allowed_status": {"needs_input"},
        "required_errors": [ErrorCode.MISSING_INPUT.value],
        "max_steps": 0,
    },
    "05_no_material": {
        "allowed_status": {"needs_input"},
        "required_errors": [ErrorCode.SEARCH_EMPTY.value],
        "required_actions": ["ask_clarify"],
    },
    "06_tool_failure": {
        "allowed_status": {"completed"},
        "required_errors": [ErrorCode.TOOL_ERROR.value],
    },
}


def load_artifact(scenario):
    results = list(RUNS_DIR.glob("{s}.*.result.json".format(s=scenario)))
    traces = list(RUNS_DIR.glob("{s}.*.trace.jsonl".format(s=scenario)))
    if not results or not traces:
        return None, None
    results.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    traces.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return (
        json.loads(results[0].read_text(encoding="utf-8")),
        [
            json.loads(line)
            for line in traces[0].read_text(encoding="utf-8").splitlines()
            if line.strip()
        ],
    )


class TestScenarioEvidence(unittest.TestCase):
    """证据层：examples/ 的产物必须满足各场景契约。"""

    def test_each_scenario_has_artifact(self):
        for scenario in SCENARIO_CONTRACTS:
            result, events = load_artifact(scenario)
            with self.subTest(scenario=scenario):
                self.assertIsNotNone(result, "缺少 {s} 的运行产物".format(s=scenario))

    def test_artifacts_match_contracts(self):
        for scenario, contract in SCENARIO_CONTRACTS.items():
            result, events = load_artifact(scenario)
            if result is None:
                continue
            with self.subTest(scenario=scenario):
                self.assertIn(result["status"], contract["allowed_status"])

                # 错误码可能记在工具结果上，也可能记在 terminal 上（如输入校验失败）
                codes = [
                    e.get("error_code")
                    for e in events
                    if e.get("event") in ("tool_result", "terminal") and e.get("error_code")
                ]
                for required in contract.get("required_errors", []):
                    self.assertIn(required, codes, "缺少预期错误码 {c}".format(c=required))

                if "allowed_errors" in contract:
                    for code in codes:
                        self.assertIn(code, contract["allowed_errors"])

                tools = [e.get("tool") for e in events if e.get("event") == "tool_result"]
                for required in contract.get("required_tools", []):
                    self.assertIn(required, tools, "缺少预期工具 {t}".format(t=required))

                actions = [
                    e.get("action") for e in events if e.get("event") == "decision" and e.get("accepted")
                ]
                for required in contract.get("required_actions", []):
                    self.assertIn(required, actions, "缺少预期动作 {a}".format(a=required))

                if "max_steps" in contract:
                    self.assertLessEqual(
                        result["steps_used"],
                        contract["max_steps"],
                        "步数超过该场景预期，说明没有及时收敛",
                    )

    def test_completed_artifacts_have_full_script(self):
        for scenario, contract in SCENARIO_CONTRACTS.items():
            result, _ = load_artifact(scenario)
            if result is None or result["status"] != "completed":
                continue
            with self.subTest(scenario=scenario):
                script = result["script"]
                for field in ("title", "shots", "estimated_total_s", "material_refs", "risks", "status"):
                    self.assertIn(field, script)
                self.assertTrue(script["shots"])


if __name__ == "__main__":
    unittest.main()
