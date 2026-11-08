"""决策源适配层测试：用假 transport 验证，离线可跑，不需要 key。"""
from __future__ import annotations

import unittest

from src.agent.llm import LLMDecider, build_messages
from src.agent.mock import RuleBasedMock
from src.domain.models import Action, Facts, Material, RiskFinding, Severity, Slot


def _facts(**overrides):
    material = Material(id="M01", title="秋季新品预热", requested_slot=Slot.parse("2026-11-10T18:00"))
    facts = Facts(material=material, material_complete=True, rules_checked=True, conflicts_checked=True)
    for key, value in overrides.items():
        setattr(facts, key, value)
    return facts


class LLMAdapterTest(unittest.TestCase):
    def _decider(self, content):
        def transport(url, payload, headers, timeout):
            self.assertIn("chat/completions", url)
            return {"choices": [{"message": {"content": content}}]}

        return LLMDecider("test-key", "https://example.com/v1", "test-model", transport=transport)

    def test_parses_action_and_reason(self):
        decider = self._decider('{"action": "reschedule", "reason": "时段冲突，先找替代"}')
        action = decider.decide(_facts(), {Action.RESCHEDULE, Action.ASK, Action.END})
        self.assertEqual(action, Action.RESCHEDULE)
        self.assertIn("替代", decider.last_reason)

    def test_out_of_set_action_falls_back(self):
        decider = self._decider('{"action": "save", "reason": "想直接保存"}')
        action = decider.decide(_facts(), {Action.ASK, Action.END})
        self.assertEqual(action, Action.ASK)  # pick() 的确定性回退
        self.assertIn("非法动作", decider.last_reason)

    def test_garbage_output_falls_back(self):
        decider = self._decider("好的，我来处理")
        action = decider.decide(_facts(), {Action.CHECK, Action.END})
        self.assertEqual(action, Action.CHECK)

    def test_transport_error_falls_back(self):
        def boom(url, payload, headers, timeout):
            raise OSError("network down")

        decider = LLMDecider("test-key", "https://example.com/v1", "m", transport=boom)
        action = decider.decide(_facts(), {Action.CHECK, Action.END})
        self.assertEqual(action, Action.CHECK)
        self.assertIn("回退", decider.last_reason)

    def test_missing_key_falls_back_without_call(self):
        decider = LLMDecider("", "https://example.com/v1", "m")
        action = decider.decide(_facts(), {Action.CHECK, Action.END})
        self.assertEqual(action, Action.CHECK)
        self.assertIn("LLM_API_KEY", decider.last_reason)

    def test_prompt_lists_only_allowed_actions(self):
        messages = build_messages(_facts(), {Action.ASK, Action.END})
        user = messages[1]["content"]
        self.assertIn("ask", user)
        self.assertNotIn("save", user)


class MockDeciderTest(unittest.TestCase):
    def test_warn_risk_asks_instead_of_saving(self):
        facts = _facts(
            findings=[RiskFinding("R002", Severity.WARN, "标题含限制级用语", "title")],
        )
        action = RuleBasedMock().decide(facts, {Action.SAVE, Action.ASK, Action.END})
        self.assertEqual(action, Action.ASK)

    def test_conflict_reschedules_first(self):
        facts = _facts()
        action = RuleBasedMock().decide(facts, {Action.RESCHEDULE, Action.ASK, Action.END})
        self.assertEqual(action, Action.RESCHEDULE)

    def test_never_returns_action_outside_allowed(self):
        mock = RuleBasedMock()
        for allowed in ({Action.FETCH, Action.END}, {Action.CHECK, Action.END}, {Action.SAVE, Action.END}):
            self.assertIn(mock.decide(_facts(), allowed), allowed)


if __name__ == "__main__":
    unittest.main(verbosity=2)
