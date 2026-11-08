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
    """按脚本逐个返回动作，用于测试护栏与失败路径。"""

    name = "scripted"

    def __init__(self, decisions):
        self.decisions = list(decisions)
        self.i = 0

    def decide(self, state):
        if self.i >= len(self.decisions):
            return ActionDecision("finish", {}, "脚本用尽，尝试结束")
        d = self.decisions[self.i]
        self.i += 1
        return d


def run(decisions=None, policy=None, task=None, model=None):
    trace = TraceWriter(None)
    m = model or ScriptedModel(decisions or [])
    loop = Loop(model=m, ctx=load_ctx(), policy=policy or Policy(), trace=trace)
    return loop.run(task or task_of()), trace


class TestLoopWithMock(unittest.TestCase):
    def test_completes_after_duration_correction(self):
        result, trace = run(model=MockModel())
        self.assertEqual(result.status, "completed")
        self.assertIsNotNone(result.script)
        self.assertLessEqual(result.script["estimated_total_s"], 30)

        # 必须出现“估算超时 → 再估算通过”的修正证据
        exceeded = [
            e
            for e in trace.events
            if e.get("event") == "tool_result" and e.get("error_code") == "DURATION_EXCEEDED"
        ]
        passed = [
            e
            for e in trace.events
            if e.get("event") == "tool_result"
            and e.get("tool") == "estimate"
            and e.get("error_code") is None
        ]
        self.assertGreaterEqual(len(exceeded), 1)
        self.assertGreaterEqual(len(passed), 1)
        self.assertGreater(exceeded[0]["step"], 0)
        self.assertGreater(passed[-1]["step"], exceeded[0]["step"])

    def test_every_decision_has_basis(self):
        _, trace = run(model=MockModel())
        decisions = [e for e in trace.events if e.get("event") == "decision"]
        self.assertGreater(len(decisions), 0)
        for ev in decisions:
            self.assertTrue(ev.get("decision_basis"), "决策缺少 decision_basis")
            self.assertIn("step", ev)
            self.assertIn("args", ev)

    def test_facts_are_traceable(self):
        result, _ = run(model=MockModel())
        ctx = load_ctx()
        for shot in result.script["shots"]:
            self.assertTrue(shot["material_ids"], "分镜未绑定素材")
            self.assertTrue(shot["fact_refs"], "分镜未引用原文事实")
            for ref in shot["fact_refs"]:
                self.assertIn(ref["material_id"], ctx.materials)
                self.assertIn(
                    ref["fact"],
                    ctx.materials[ref["material_id"]]["facts"],
                    "引用的事实不在素材原文中",
                )
        self.assertTrue(result.script["material_refs"])


class TestGuards(unittest.TestCase):
    def test_finish_rejected_without_draft(self):
        """没有草案就 finish，护栏应先拦“没有脚本草案”。"""
        result, trace = run([ActionDecision("finish", {}, "想直接结束")], policy=Policy(max_steps=3))
        rejects = [e for e in trace.events if e.get("event") == "decision" and not e.get("accepted")]
        self.assertGreaterEqual(len(rejects), 1)
        self.assertIn("没有脚本草案", rejects[0]["reject_reason"])
        self.assertEqual(result.status, "incomplete")
        self.assertEqual(result.error_code, ErrorCode.STEP_LIMIT.value)

    def test_finish_rejected_without_estimate(self):
        """有草案但未经 estimate，finish 必须被驳回，不能白拿结果。"""
        compose = ActionDecision(
            "compose",
            {"title": "t", "shots": [{"shot_id": "s1", "narration": "支持字幕", "material_ids": ["P01"]}]},
            "写稿",
        )
        result, trace = run(
            [compose, ActionDecision("finish", {}, "直接结束")], policy=Policy(max_steps=4)
        )
        rejects = [e for e in trace.events if e.get("event") == "decision" and not e.get("accepted")]
        self.assertIn("时长估算未通过", rejects[0]["reject_reason"])
        self.assertEqual(result.status, "incomplete")

    def test_step_limit_returns_incomplete(self):
        result, _ = run(model=MockModel(), policy=Policy(max_steps=2))
        self.assertEqual(result.status, "incomplete")
        self.assertEqual(result.error_code, ErrorCode.STEP_LIMIT.value)

    def test_tool_failure_is_recorded(self):
        """工具失败必须留下 TOOL_ERROR 观察，而不是崩溃或静默。"""
        bad = ActionDecision(
            "compose",
            {"title": "t", "shots": [{"shot_id": "s1", "narration": "支持字幕", "material_ids": ["P99"]}]},
            "引用一个不存在的素材",
        )
        result, trace = run([bad], policy=Policy(max_steps=3))
        tool_results = [e for e in trace.events if e.get("event") == "tool_result"]
        self.assertEqual(tool_results[0]["error_code"], ErrorCode.TOOL_ERROR.value)
        self.assertEqual(result.status, "incomplete")

    def test_missing_required_input(self):
        result, _ = run(task=task_of(platform=""))
        self.assertEqual(result.status, "needs_input")
        self.assertEqual(result.error_code, ErrorCode.MISSING_INPUT.value)
        self.assertIn("platform", result.reason)


class TestDeterministicCorrection(unittest.TestCase):
    """修正路径必须可复现：同样输入跑两遍，脚本必须逐字节一致。"""

    def test_mock_correction_goes_through_compress_and_repeats(self):
        r1, t1 = run(model=MockModel())
        r2, t2 = run(model=MockModel())
        self.assertEqual(r1.status, "completed")
        self.assertEqual(r1.script, r2.script, "同样输入两次结果不一致，说明修正不可复现")

        tools = [e.get("tool") for e in t1.events if e.get("event") == "tool_result"]
        self.assertIn("compress", tools, "修正应走确定性压缩工具")

        first_fail = next(
            i
            for i, e in enumerate(t1.events)
            if e.get("event") == "tool_result"
            and e.get("tool") == "estimate"
            and e.get("error_code") == "DURATION_EXCEEDED"
        )
        compress_at = tools.index("compress")
        last_ok = max(
            i
            for i, e in enumerate(t1.events)
            if e.get("event") == "tool_result"
            and e.get("tool") == "estimate"
            and e.get("error_code") is None
        )
        self.assertLess(first_fail, len(t1.events))
        self.assertLess(first_fail, last_ok, "必须先超时、后通过")
        self.assertGreater(compress_at, 0)

    def test_compressed_draft_keeps_fact_traceability(self):
        result, _ = run(model=MockModel())
        ctx = load_ctx()
        for shot in result.script["shots"]:
            self.assertTrue(shot["fact_refs"])
            for ref in shot["fact_refs"]:
                self.assertIn(ref["fact"], ctx.materials[ref["material_id"]]["facts"])


if __name__ == "__main__":
    unittest.main()
