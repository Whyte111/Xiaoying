"""最小闭环的行为断言。零第三方依赖，python -m unittest 直接跑。"""
from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from src import guards
from src.decision import MockPolicy
from src.engine import Engine
from src.store import load_scenario
from src.tools import Toolbox
from src.types import (
    STATUS_COMPLETED,
    STATUS_INCOMPLETE,
    STATUS_STOPPED_BY_LIMIT,
    STEP_FAILED,
    STEP_PENDING,
    STEP_SKIPPED,
    STEP_SUCCEEDED,
    TaskInput,
    ToolResult,
)


class AlwaysFailToolbox(Toolbox):
    """指定工具永久失败，用于验证"失败步骤不得记成功"。"""

    def __init__(self, scenario, out_dir, tool: str) -> None:
        super().__init__(scenario, out_dir)
        self.fail_tools = set()
        self._always_fail = tool

    def call(self, name: str, args) -> ToolResult:
        if name == self._always_fail:
            return ToolResult(
                ok=False, tool=name, args=args,
                error_code="BROKEN", error_msg="永久故障（测试用）",
            )
        return super().call(name, args)


class ClosedLoopTest(unittest.TestCase):
    def setUp(self) -> None:
        self.scenario = load_scenario()
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _run(self, topic="秋季校园活动", budget=10.0, deadline="2026-11-20", toolbox=None, **kwargs):
        tb = toolbox or Toolbox(self.scenario, self.out)
        engine = Engine(self.scenario, MockPolicy(self.scenario), toolbox=tb, out_dir=self.out, **kwargs)
        return engine.run(TaskInput(topic=topic, budget=budget, deadline=deadline))

    # 1. 正常路径：超预算 -> 改选 -> 保存
    def test_normal_with_replan(self):
        r = self._run(budget=10.0)
        self.assertEqual(r.status, STATUS_COMPLETED)
        self.assertEqual(r.total_cost, 8.6)
        self.assertEqual(r.plan_cost, 8.0)
        self.assertEqual(r.tool_fee, 0.6)
        options = [x.data["option_id"] for x in r.tool_results if x.tool == "estimate_cost"]
        self.assertEqual(options, ["A", "B"])  # 先 A 后改选 B
        self.assertIsNotNone(r.saved_artifact)
        self.assertTrue(Path(r.saved_artifact["path"]).exists())
        self.assertTrue(all(p.status == STEP_SUCCEEDED for p in r.plan))

    # 2. 预算超限：不得声称完成，不得产生保存
    def test_budget_exceeded(self):
        r = self._run(budget=5.0)
        self.assertEqual(r.status, STATUS_INCOMPLETE)
        self.assertIsNone(r.saved_artifact)
        self.assertIn("缺口", r.blocked_reason)
        self.assertTrue(any(v.code == guards.BUDGET_EXCEEDED for v in r.violations))
        s4 = next(p for p in r.plan if p.step_id == "s4")
        self.assertEqual(s4.status, STEP_SKIPPED)
        self.assertEqual(r.total_cost, r.tool_fee)  # 未保存不计方案成本

    # 3. 缺预算：直接阻断，不产生任何工具调用
    def test_missing_budget(self):
        r = self._run(budget=None)
        self.assertEqual(r.status, STATUS_INCOMPLETE)
        self.assertEqual(r.tool_calls, 0)
        self.assertEqual(r.tool_results, [])
        self.assertIn("缺少预算", r.blocked_reason)

    # 3b. 缺截止时间：同样阻断
    def test_missing_deadline(self):
        r = self._run(budget=10.0, deadline=None)
        self.assertEqual(r.status, STATUS_INCOMPLETE)
        self.assertEqual(r.tool_calls, 0)
        self.assertIn("缺少截止时间", r.blocked_reason)

    # 3c. 截止时间格式非法：阻断并说明应填格式
    def test_invalid_deadline(self):
        r = self._run(budget=10.0, deadline="下周")
        self.assertEqual(r.status, STATUS_INCOMPLETE)
        self.assertEqual(r.tool_calls, 0)
        self.assertIn("格式无法解析", r.blocked_reason)

    # 3d. 时间核算未启用必须显式暴露，而不是假装算过
    def test_time_not_checked_is_explicit(self):
        r = self._run(budget=10.0)
        self.assertFalse(r.time_checked)

    # 4. 注入一次失败：降级后仍能完成，失败记录保留
    def test_injected_failure_recovers(self):
        tb = Toolbox(self.scenario, self.out)
        tb.fail_tools = {"generate_script"}
        r = self._run(budget=10.0, toolbox=tb)
        self.assertEqual(r.status, STATUS_COMPLETED)
        failed = [x for x in r.tool_results if not x.ok]
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0].error_code, "INJECTED_FAILURE")
        s2 = next(p for p in r.plan if p.step_id == "s2")
        self.assertEqual(s2.status, STEP_SUCCEEDED)  # 重试后成功

    # 5. 工具永久失败：步骤保持 failed，整体未完成
    def test_permanent_failure_not_marked_success(self):
        tb = AlwaysFailToolbox(self.scenario, self.out, "generate_script")
        r = self._run(budget=10.0, toolbox=tb)
        self.assertEqual(r.status, STATUS_INCOMPLETE)
        s2 = next(p for p in r.plan if p.step_id == "s2")
        self.assertEqual(s2.status, STEP_FAILED)
        s3 = next(p for p in r.plan if p.step_id == "s3")
        s4 = next(p for p in r.plan if p.step_id == "s4")
        self.assertEqual((s3.status, s4.status), (STEP_SKIPPED, STEP_SKIPPED))
        self.assertIsNone(r.saved_artifact)
        self.assertIn("脚本生成连续失败", r.blocked_reason)
        self.assertTrue(all(not x.ok for x in r.tool_results if x.tool == "generate_script"))

    # 6. 触顶：调用数上限必须给出明确未完成状态
    def test_tool_call_limit(self):
        r = self._run(budget=10.0, max_tool_calls=2)
        self.assertEqual(r.status, STATUS_STOPPED_BY_LIMIT)
        self.assertEqual(r.tool_calls, 2)
        self.assertIn("上限", r.blocked_reason)
        self.assertIsNone(r.saved_artifact)

    # 7. 费用口径自洽
    def test_cost_identity(self):
        r = self._run(budget=10.0)
        tool_sum = round(sum(x.cost for x in r.tool_results), 2)
        self.assertAlmostEqual(r.tool_fee, tool_sum, places=2)
        self.assertAlmostEqual(r.total_cost, round(r.plan_cost + r.tool_fee, 2), places=2)

    # 8. trace 必须能复现"观察后改动作"
    def test_trace_shows_adaptation(self):
        self._run(budget=10.0)
        trace = json.loads((self.out / "trace.json").read_text(encoding="utf-8"))
        estimates = [e for e in trace if e.get("tool") == "estimate_cost"]
        self.assertEqual(len(estimates), 2)
        self.assertIn("改选", estimates[1]["reason"])

    # 9. 重复执行：不重复调用工具、不重复保存
    def test_repeat_execution_is_idempotent(self):
        first = self._run(budget=10.0)
        second = self._run(budget=10.0)
        self.assertTrue(second.reused)
        self.assertEqual(second.tool_calls, 0)
        self.assertEqual(
            second.saved_artifact["artifact_id"], first.saved_artifact["artifact_id"]
        )
        self.assertEqual(len(list(self.out.glob("plan-*.json"))), 1)


    # 11. 预算边界：保存后不得重复计入保存费
    def test_budget_boundary_after_save(self):
        # 用能精确命中素材的主题，保证只检索一次（花费 0.5 + 方案 8 + 保存 0.1 = 8.6）
        for topic, budget in (("秋季", 8.6), ("校园", 8.61)):
            with self.subTest(budget=budget):
                r = self._run(budget=budget, topic=topic)
                self.assertEqual(r.status, STATUS_COMPLETED)
                self.assertIsNotNone(r.saved_artifact)
                self.assertEqual(r.total_cost, 8.6)

    def test_budget_just_below_is_rejected(self):
        r = self._run(budget=8.59, topic="活动")
        self.assertEqual(r.status, STATUS_INCOMPLETE)
        self.assertIsNone(r.saved_artifact)

    # 10. 失败续跑：已成功的步骤不重跑，只补失败那一步
    def test_resume_skips_succeeded_steps(self):
        broken = AlwaysFailToolbox(self.scenario, self.out, "save_plan")
        first = self._run(budget=10.0, toolbox=broken)
        self.assertEqual(first.status, STATUS_INCOMPLETE)
        self.assertEqual(first.plan[0].status, STEP_SUCCEEDED)
        self.assertEqual(first.plan[3].status, STEP_FAILED)

        second = self._run(budget=10.0)
        self.assertTrue(second.resumed)
        self.assertEqual(second.status, STATUS_COMPLETED)
        self.assertEqual(second.tool_calls, 1)  # 只补跑 save_plan
        self.assertEqual(second.saved_artifact["option_id"], "B")


class TimeoutTest(unittest.TestCase):
    def test_tool_timeout_is_failure(self):
        scenario = load_scenario()

        class SlowToolbox(Toolbox):
            def _search_material(self, args):
                time.sleep(0.2)
                return {"count": 0, "materials": []}

        with tempfile.TemporaryDirectory() as d:
            tb = SlowToolbox(scenario, Path(d), timeout_s=0.05)
            r = tb.call("search_material", {"query": "x"})
        self.assertFalse(r.ok)
        self.assertEqual(r.error_code, "TIMEOUT")
        self.assertGreater(r.duration_ms, 50)


class GuardsTest(unittest.TestCase):
    def test_budget_gap(self):
        vs = guards.check_budget(0.5, {"option_id": "B", "total_cost": 8.0}, 5.0, 0.1)
        self.assertEqual(len(vs), 1)
        self.assertIn("缺口 3.60", vs[0].detail)

    def test_deps_unmet(self):
        from src.types import PlanItem

        plan = [
            PlanItem("s1", "a", "x", []),
            PlanItem("s2", "b", "y", ["s1"]),
        ]
        plan[0].status = STEP_PENDING
        plan[1].status = STEP_SUCCEEDED
        vs = guards.check_deps(plan)
        self.assertEqual(len(vs), 1)
        self.assertEqual(vs[0].code, guards.DEP_UNMET)


if __name__ == "__main__":
    unittest.main()
