"""安全/质量回归测试：输入校验、畸形输入、权限边界。

每一条都对应审计里列出的一个问题，防止回归。
"""

import json
import unittest

from src.agent.loop import Loop
from src.agent.policy import Policy
from src.config import load_ctx
from src.domain.errors import ErrorCode
from src.domain.schemas import ActionDecision, TaskInput
from src.llm.mock_client import MockModel
from src.observability.trace import TraceWriter


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


def run(decisions=None, policy=None, task=None, model=None):
    trace = TraceWriter(None)
    m = model or ScriptedModel(decisions or [])
    loop = Loop(model=m, ctx=load_ctx(), policy=policy or Policy(), trace=trace)
    return loop.run(task or task_of()), trace


class TestInputValidation(unittest.TestCase):
    """P0-3：畸形输入必须在入口被拒，而不是崩溃或静默变形。"""

    def test_material_ids_as_string_is_rejected(self):
        with self.assertRaises(ValueError):
            TaskInput.from_dict({"topic": "t", "platform": "douyin", "target_duration_s": 30, "material_ids": "P01"})

    def test_target_duration_not_number_is_rejected(self):
        with self.assertRaises(ValueError):
            TaskInput.from_dict({"topic": "t", "platform": "douyin", "target_duration_s": "abc"})

    def test_target_duration_bool_is_rejected(self):
        with self.assertRaises(ValueError):
            TaskInput.from_dict({"topic": "t", "platform": "douyin", "target_duration_s": True})

    def test_nan_duration_is_rejected(self):
        with self.assertRaises(ValueError):
            TaskInput.from_dict({"topic": "t", "platform": "douyin", "target_duration_s": float("nan")})

    def test_infinite_duration_is_rejected(self):
        with self.assertRaises(ValueError):
            TaskInput.from_dict({"topic": "t", "platform": "douyin", "target_duration_s": float("inf")})

    def test_non_object_input_is_rejected(self):
        with self.assertRaises(ValueError):
            TaskInput.from_dict(["not", "an", "object"])

    def test_negative_duration_is_reported_as_problem(self):
        t = TaskInput.from_dict({"topic": "t", "platform": "douyin", "target_duration_s": -5})
        self.assertTrue(any("target_duration_s" in p for p in t.problems()))

    def test_absurd_duration_is_reported_as_problem(self):
        t = TaskInput.from_dict({"topic": "t", "platform": "douyin", "target_duration_s": 100000})
        self.assertTrue(any("上限" in p for p in t.problems()))

    def test_invalid_task_terminates_without_calling_model(self):
        class ExplodingModel:
            name = "exploding"

            def decide(self, state):
                raise AssertionError("模型不应被调用")

        result, _ = run(task=task_of(target_duration_s=-1), model=ExplodingModel())
        self.assertEqual(result.status, "needs_input")
        self.assertEqual(result.error_code, ErrorCode.MISSING_INPUT.value)
        self.assertEqual(result.steps_used, 0)


class TestComposeStructuralGuards(unittest.TestCase):
    """P0-1 / P0-2 / P1-3：结构畸形必须在 compose 阶段挡掉。"""

    @staticmethod
    def compose(shots, title="t"):
        return ActionDecision("compose", {"title": title, "shots": shots}, "提交草案")

    def _first_error(self, decision):
        result, trace = run([decision], policy=Policy(max_steps=2))
        results = [e for e in trace.events if e.get("event") == "tool_result"]
        return results[0]["error_code"] if results else None

    def test_duplicate_shot_id_rejected(self):
        shots = [
            {"shot_id": "s1", "narration": "支持字幕", "material_ids": ["P01"], "fact_refs": [{"material_id": "P01", "fact": "支持字幕"}]},
            {"shot_id": "s1", "narration": "支持横竖屏切换", "material_ids": ["P01"], "fact_refs": [{"material_id": "P01", "fact": "支持横竖屏切换"}]},
        ]
        self.assertEqual(self._first_error(self.compose(shots)), ErrorCode.TOOL_ERROR.value)

    def test_empty_narration_rejected(self):
        shots = [{"shot_id": "s1", "narration": "   ", "material_ids": ["P01"]}]
        self.assertEqual(self._first_error(self.compose(shots)), ErrorCode.TOOL_ERROR.value)

    def test_too_many_shots_rejected(self):
        shots = [
            {"shot_id": "s{n}".format(n=i), "narration": "支持字幕", "material_ids": ["P01"]}
            for i in range(21)
        ]
        self.assertEqual(self._first_error(self.compose(shots)), ErrorCode.TOOL_ERROR.value)

    def test_oversized_narration_rejected(self):
        shots = [{"shot_id": "s1", "narration": "字" * 5000, "material_ids": ["P01"]}]
        self.assertEqual(self._first_error(self.compose(shots)), ErrorCode.TOOL_ERROR.value)


class TestPolicyAndLimits(unittest.TestCase):
    """P1-1 / 白名单：护栏本身要能兜住模型的越界动作。"""

    def test_unknown_action_rejected(self):
        result, trace = run(
            [ActionDecision("delete_everything", {}, "越界尝试")], policy=Policy(max_steps=2)
        )
        rejects = [e for e in trace.events if e.get("event") == "decision" and not e.get("accepted")]
        self.assertIn("白名单", rejects[0]["reject_reason"])
        self.assertEqual(result.status, "incomplete")

    def test_max_steps_is_clamped(self):
        from src.cli import clamp_max_steps

        self.assertEqual(clamp_max_steps(999999), 100)
        self.assertEqual(clamp_max_steps(0), 1)
        self.assertEqual(clamp_max_steps(-5), 1)
        self.assertEqual(clamp_max_steps(8), 8)

    def test_step_limit_is_always_bounded(self):
        result, _ = run(model=MockModel(), policy=Policy(max_steps=1))
        self.assertEqual(result.error_code, ErrorCode.STEP_LIMIT.value)
        self.assertLessEqual(result.steps_used, 1)


if __name__ == "__main__":
    unittest.main()
