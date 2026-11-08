"""异常路径测试：每一条都必须收敛到**显式终态**，不允许静默、崩溃或空转。

对照表（题面要求 + 审计缺口）：
    ask_clarify        -> needs_input
    修正次数用尽        -> incomplete / UNSATISFIABLE
    工具调用上限        -> 护栏拒绝
    重复动作            -> 护栏拒绝
    模型调用失败        -> failed / MODEL_ERROR
    模型连续非法格式    -> ModelError
"""

import unittest

from src.agent.loop import Loop
from src.agent.policy import Policy
from src.config import load_ctx
from src.domain.errors import ErrorCode
from src.domain.schemas import ActionDecision, TaskInput
from src.llm.base import ModelError
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


class ExplodingModel:
    name = "exploding"

    def decide(self, state):
        raise ModelError("模拟模型不可用")


def run(decisions=None, policy=None, model=None, task=None):
    trace = TraceWriter(None)
    m = model or ScriptedModel(decisions or [])
    loop = Loop(model=m, ctx=load_ctx(), policy=policy or Policy(), trace=trace)
    return loop.run(task or task_of()), trace


def long_compose(chars=300):
    return ActionDecision(
        "compose",
        {
            "title": "t",
            "shots": [
                {
                    "shot_id": "s1",
                    "narration": "字" * chars,
                    "material_ids": ["P01"],
                    "fact_refs": [{"material_id": "P01", "fact": "支持字幕"}],
                }
            ],
        },
        "提交一个明显超长的草案",
    )


class TestTerminalPaths(unittest.TestCase):
    def test_ask_clarify_terminates_as_needs_input(self):
        decision = ActionDecision("ask_clarify", {"question": "请补充素材"}, "没有素材无法继续")
        result, trace = run([decision])
        self.assertEqual(result.status, "needs_input")
        self.assertEqual(result.error_code, ErrorCode.SEARCH_EMPTY.value)
        self.assertIn("请补充素材", result.reason)
        self.assertEqual(result.tool_calls_used, 0, "ask_clarify 不应消耗工具调用")

    def test_model_failure_terminates_as_failed(self):
        result, _ = run(model=ExplodingModel())
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error_code, ErrorCode.MODEL_ERROR.value)

    def test_exhausted_fixes_yield_unsatisfiable(self):
        decisions = [long_compose()]
        # 反复估算失败，直到修正次数用尽
        decisions += [ActionDecision("estimate", {}, "重新估算")] * 5
        result, trace = run(decisions, policy=Policy(max_fixes=1))
        self.assertEqual(result.status, "incomplete")
        self.assertEqual(result.error_code, ErrorCode.UNSATISFIABLE.value)
        self.assertIsNotNone(result.partial, "失败时应带回已完成部分")
        exceeded = [
            e
            for e in trace.events
            if e.get("event") == "tool_result"
            and e.get("error_code") == ErrorCode.DURATION_EXCEEDED.value
        ]
        self.assertGreaterEqual(len(exceeded), 1)

    def test_unsatisfiable_is_bounded(self):
        decisions = [long_compose()] + [ActionDecision("estimate", {}, "x")] * 20
        result, _ = run(decisions, policy=Policy(max_fixes=1, max_steps=20))
        self.assertEqual(result.error_code, ErrorCode.UNSATISFIABLE.value)
        self.assertLessEqual(result.steps_used, 4, "修正失败必须尽快收敛，不能空转到步数上限")


class TestGuardsUnderStress(unittest.TestCase):
    def test_tool_call_cap_is_enforced(self):
        decisions = [ActionDecision("search", {"query": "智能剪辑器"}, "检索")] * 5
        result, trace = run(decisions, policy=Policy(max_tool_calls=1, max_steps=5))
        reasons = [
            e.get("reject_reason")
            for e in trace.events
            if e.get("event") == "decision" and not e.get("accepted")
        ]
        self.assertTrue(any("工具调用次数已达上限" in r for r in reasons), reasons)
        self.assertLessEqual(result.tool_calls_used, 1)

    def test_repeated_identical_action_is_rejected(self):
        same = ActionDecision("search", {"query": "同一个查询"}, "重复")
        result, trace = run([same, same, same], policy=Policy(max_steps=5))
        reasons = [
            e.get("reject_reason")
            for e in trace.events
            if e.get("event") == "decision" and not e.get("accepted")
        ]
        self.assertTrue(any("重复" in r for r in reasons), reasons)

    def test_estimate_without_draft_is_rejected(self):
        result, trace = run([ActionDecision("estimate", {}, "没稿子就估算")], policy=Policy(max_steps=2))
        rejects = [e for e in trace.events if e.get("event") == "decision" and not e.get("accepted")]
        self.assertIn("脚本草案", rejects[0]["reject_reason"])


class TestModelOutputPathology(unittest.TestCase):
    def test_persistent_garbage_output_raises_model_error(self):
        from src.llm.deepseek_client import DeepSeekModel

        m = DeepSeekModel.__new__(DeepSeekModel)
        m.api_key = "x"
        m.model = "m"
        m.base_url = "http://127.0.0.1:1"
        m.timeout = 1
        m._post = lambda messages: "这不是 JSON"
        with self.assertRaises(ModelError):
            m.decide(_state_for(m))

    def test_out_of_whitelist_action_is_rejected_by_policy(self):
        result, trace = run(
            [ActionDecision("rm_rf", {"path": "/"}, "越界")], policy=Policy(max_steps=2)
        )
        rejects = [e for e in trace.events if e.get("event") == "decision" and not e.get("accepted")]
        self.assertIn("白名单", rejects[0]["reject_reason"])


def _state_for(model):
    from src.domain.schemas import LoopState

    return LoopState(task=task_of())


if __name__ == "__main__":
    unittest.main()
