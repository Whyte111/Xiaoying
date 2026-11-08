"""安全问题回归测试：对应审计报告中的 P0 / P1 项。"""
from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from src.decision import MockPolicy
from src.engine import Engine
from src.llm import LLMDecisionPolicy
from src.state import fingerprint, scenario_digest
from src.store import load_scenario
from src.tools import Toolbox
from src.types import STATUS_COMPLETED, STATUS_INCOMPLETE, STATUS_STOPPED_BY_LIMIT, TaskInput

SCENARIO = load_scenario()


def run(scenario, budget, topic="主题", deadline="2026-11-20", out=None, policy=None, **kw):
    d = Path(out or tempfile.mkdtemp())
    return Engine(
        scenario,
        policy or MockPolicy(scenario),
        toolbox=Toolbox(scenario, d),
        out_dir=d,
        **kw,
    ).run(TaskInput(topic, budget, deadline))


class P0Test(unittest.TestCase):
    def test_finish_without_save_is_rejected(self):
        """模型直接 finish 不能算完成。"""
        policy = LLMDecisionPolicy(
            SCENARIO,
            transport=lambda payload: '{"type":"finish","reason":"我觉得做完了"}',
            api_key="k",
        )
        r = run(SCENARIO, 10.0, policy=policy)
        self.assertEqual(r.status, STATUS_INCOMPLETE)
        self.assertIsNone(r.saved_artifact)
        self.assertIn("尚未保存", r.blocked_reason)

    def test_budget_must_be_finite_positive(self):
        for bad in (float("nan"), float("inf"), -3.0, 0.0):
            with self.subTest(budget=bad):
                r = run(SCENARIO, bad)
                self.assertEqual(r.status, STATUS_INCOMPLETE)
                self.assertEqual(r.tool_calls, 0)
                self.assertIn("有限正数", r.blocked_reason)

    def test_policy_exception_returns_incomplete(self):
        """素材缺字段导致策略抛 KeyError，不应穿透成 traceback。"""
        broken = {**SCENARIO, "materials": [{"title": "没有 id"}]}
        r = run(broken, 10.0)
        self.assertEqual(r.status, STATUS_INCOMPLETE)
        self.assertIn("决策器异常", r.blocked_reason)

    def test_corrupt_state_entry_is_ignored(self):
        d = Path(tempfile.mkdtemp())
        task = TaskInput("主题", 10.0, "2026-11-20")
        run(SCENARIO, 10.0, out=d)
        fp = fingerprint(task, "mock", scenario_digest(SCENARIO))
        store = d / "state.json"
        data = json.loads(store.read_text(encoding="utf-8"))
        data[fp] = {"status": "completed"}  # 缺 result
        store.write_text(json.dumps(data), encoding="utf-8")

        r = run(SCENARIO, 10.0, out=d)
        self.assertEqual(r.status, STATUS_COMPLETED)
        self.assertFalse(r.reused)  # 丢弃坏记录，重新执行

    def test_side_effect_timeout_is_outcome_unknown(self):
        """保存超时可能已落盘，不能当成普通失败重试。"""

        class SlowSave(Toolbox):
            def _save_plan(self, args):
                time.sleep(0.05)
                return super()._save_plan(args)

        d = Path(tempfile.mkdtemp())
        engine = Engine(
            SCENARIO,
            MockPolicy(SCENARIO),
            toolbox=SlowSave(SCENARIO, d, timeout_s=0.01),
            out_dir=d,
        )
        r = engine.run(TaskInput("主题", 10.0, "2026-11-20"))
        self.assertEqual(r.status, STATUS_INCOMPLETE)
        codes = [x.error_code for x in r.tool_results]
        self.assertIn("OUTCOME_UNKNOWN", codes)
        self.assertIn("未知", r.blocked_reason)


class P1Test(unittest.TestCase):
    def test_empty_materials_reason_is_honest(self):
        empty = {**SCENARIO, "materials": []}
        r = run(empty, 10.0)
        self.assertEqual(r.status, STATUS_INCOMPLETE)
        self.assertIn("没有匹配", r.blocked_reason)
        self.assertNotIn("连续失败", r.blocked_reason)

    def test_max_steps_has_reason(self):
        r = run(SCENARIO, 10.0, max_steps=1)
        self.assertEqual(r.status, STATUS_STOPPED_BY_LIMIT)
        self.assertIn("步数上限", r.blocked_reason)

    def test_llm_fallback_is_counted(self):
        policy = LLMDecisionPolicy(
            SCENARIO,
            transport=lambda payload: (_ for _ in ()).throw(RuntimeError("endpoint down")),
            api_key="k",
        )
        r = run(SCENARIO, 10.0, policy=policy)
        self.assertEqual(r.provider, "llm")
        self.assertGreater(r.fallback_calls, 0)
        self.assertGreaterEqual(r.fallback_calls, r.tool_calls)


class DataValidationTest(unittest.TestCase):
    """场景数据与输入的校验（P1）。"""

    def _scenario_file(self, data):
        d = Path(tempfile.mkdtemp())
        p = d / "scenario.json"
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return p

    def test_missing_save_fee_rejected(self):
        bad = json.loads(json.dumps(SCENARIO))
        bad["fee_table"].pop("save_plan")
        with self.assertRaises(ValueError) as ctx:
            load_scenario(self._scenario_file(bad))
        self.assertIn("save_plan", str(ctx.exception))

    def test_material_without_id_rejected(self):
        bad = json.loads(json.dumps(SCENARIO))
        bad["materials"][0].pop("id")
        with self.assertRaises(ValueError) as ctx:
            load_scenario(self._scenario_file(bad))
        self.assertIn("materials[0].id", str(ctx.exception))

    def test_duplicate_option_rejected(self):
        bad = json.loads(json.dumps(SCENARIO))
        bad["plan_options"][1]["id"] = bad["plan_options"][0]["id"]
        with self.assertRaises(ValueError) as ctx:
            load_scenario(self._scenario_file(bad))
        self.assertIn("重复", str(ctx.exception))

    def test_invalid_json_rejected(self):
        d = Path(tempfile.mkdtemp())
        p = d / "scenario.json"
        p.write_text("{broken", encoding="utf-8")
        with self.assertRaises(ValueError):
            load_scenario(p)

    def test_scenario_change_invalidates_cached_result(self):
        d = Path(tempfile.mkdtemp())
        first = run(SCENARIO, 10.0, out=d)
        changed = json.loads(json.dumps(SCENARIO))
        for opt in changed["plan_options"]:
            if opt["id"] == "A":
                opt["est_cost"] = 9.0
        second = run(changed, 10.0, out=d)
        self.assertFalse(second.reused)
        self.assertEqual(second.plan_cost, 9.0)  # A 现在进预算，重新选了 A

    def test_topic_too_long_rejected(self):
        r = run(SCENARIO, 10.0, topic="x" * 2001)
        self.assertEqual(r.status, STATUS_INCOMPLETE)
        self.assertEqual(r.tool_calls, 0)
        self.assertIn("主题过长", r.blocked_reason)

    def test_long_topic_is_truncated_in_result(self):
        r = run(SCENARIO, 10.0, topic="x" * 5000)
        self.assertEqual(r.status, STATUS_INCOMPLETE)
        self.assertIn("已截断", r.topic)
        self.assertLess(len(r.topic), 2100)
        self.assertIn("主题过长", r.blocked_reason)

    def test_cli_returns_2_on_data_error(self):
        from unittest import mock

        from src import cli

        with mock.patch("src.cli.load_scenario", side_effect=ValueError("场景数据不合法")):
            rc = cli.main(["--topic", "t", "--budget", "10", "--deadline", "2026-11-20"])
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
