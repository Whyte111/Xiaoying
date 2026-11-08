"""LLM 决策器的离线测试：用假 transport 验证解析、校验与降级。"""
from __future__ import annotations

import json
import unittest

from src.decision import MockPolicy
from src.llm import LLMDecisionPolicy
from src.store import load_scenario
from src.types import ACTION_CALL_TOOL, ACTION_STOP, Observation


def make_obs(**kwargs) -> Observation:
    base = dict(round=1, topic="秋季校园活动", budget_total=10.0, spent=0.0, time_left=float("inf"))
    base.update(kwargs)
    return Observation(**base)


class LLMPolicyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.scenario = load_scenario()

    def _policy(self, replies):
        queue = list(replies)
        captured = []

        def transport(payload):
            captured.append(payload)
            item = queue.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        policy = LLMDecisionPolicy(
            self.scenario, transport=transport, api_key="test-key", model="test-model"
        )
        return policy, captured

    def test_valid_action_is_used(self):
        policy, _ = self._policy([
            json.dumps(
                {
                    "type": "call_tool",
                    "tool": "search_material",
                    "args": {"query": "秋季"},
                    "reason": "先检索",
                }
            )
        ])
        action = policy.next(make_obs())
        self.assertEqual(action.type, ACTION_CALL_TOOL)
        self.assertEqual(action.tool, "search_material")
        self.assertEqual(action.args, {"query": "秋季"})
        self.assertEqual(policy.last_source, "llm")
        self.assertEqual(policy.fallback_calls, 0)

    def test_code_fence_is_tolerated(self):
        policy, _ = self._policy(['```json\n{"type":"stop","reason":"预算不够"}\n```'])
        action = policy.next(make_obs())
        self.assertEqual(action.type, ACTION_STOP)
        self.assertIn("预算不够", action.reason)

    def test_invalid_json_falls_back(self):
        policy, _ = self._policy(["我觉得应该先检索素材"])
        action = policy.next(make_obs())
        self.assertEqual(policy.last_source, "fallback")
        self.assertEqual(policy.fallback_calls, 1)
        self.assertIn("JSON", policy.last_error or "")
        # 降级动作来自 MockPolicy：无素材时先检索
        self.assertEqual(action.tool, "search_material")

    def test_unknown_tool_falls_back(self):
        policy, _ = self._policy([
            json.dumps({"type": "call_tool", "tool": "delete_everything", "args": {}})
        ])
        action = policy.next(make_obs())
        self.assertEqual(policy.last_source, "fallback")
        self.assertEqual(action.tool, "search_material")

    def test_transport_error_falls_back(self):
        policy, _ = self._policy([RuntimeError("HTTP 401: unauthorized")])
        action = policy.next(make_obs())
        self.assertEqual(policy.last_source, "fallback")
        self.assertIn("401", policy.last_error or "")
        self.assertEqual(action.tool, "search_material")

    def test_payload_has_no_secret(self):
        policy, captured = self._policy([json.dumps({"type": "stop", "reason": "x"})])
        policy.next(make_obs())
        payload_text = json.dumps(captured[0], ensure_ascii=False)
        self.assertNotIn("test-key", payload_text)
        self.assertEqual(captured[0]["model"], "test-model")

    def test_fallback_matches_mock_decision(self):
        obs = make_obs()
        expected = MockPolicy(self.scenario).next(obs)
        policy, _ = self._policy(["garbage"])
        got = policy.next(obs)
        self.assertEqual((got.type, got.tool), (expected.type, expected.tool))


if __name__ == "__main__":
    unittest.main()
