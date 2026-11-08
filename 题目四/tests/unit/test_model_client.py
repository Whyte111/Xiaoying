"""模型客户端健壮性：瞬时故障重试、非瞬时故障快速失败、密钥不外泄。"""

import unittest
import urllib.error
from unittest import mock

from src.llm.base import ModelError
from src.llm.deepseek_client import DeepSeekModel, _parse_decision


def bare_model():
    """构造一个不需要真实密钥的实例，只测网络层行为。"""
    m = DeepSeekModel.__new__(DeepSeekModel)
    m.api_key = "fake-key-should-never-leak"
    m.model = "fake-model"
    m.base_url = "http://127.0.0.1:1"
    m.timeout = 1
    return m


class TestRetry(unittest.TestCase):
    def test_transient_url_error_is_retried_once(self):
        m = bare_model()
        calls = {"n": 0}

        def flaky(messages):
            calls["n"] += 1
            if calls["n"] == 1:
                raise urllib.error.URLError("connection reset")
            return '{"action": "finish", "args": {}, "decision_basis": "ok"}'

        m._post = flaky
        with mock.patch("src.llm.deepseek_client.time.sleep"):
            out = m._chat([{"role": "user", "content": "x"}])
        self.assertEqual(calls["n"], 2)
        self.assertIn("finish", out)

    def test_persistent_url_error_raises_model_error(self):
        m = bare_model()

        def always_down(messages):
            raise urllib.error.URLError("down")

        m._post = always_down
        with mock.patch("src.llm.deepseek_client.time.sleep"):
            with self.assertRaises(ModelError):
                m._chat([{"role": "user", "content": "x"}])

    def test_4xx_is_not_retried(self):
        m = bare_model()
        calls = {"n": 0}

        def forbidden(messages):
            calls["n"] += 1
            raise urllib.error.HTTPError("http://x", 403, "forbidden", None, None)

        m._post = forbidden
        with mock.patch("src.llm.deepseek_client.time.sleep"):
            with self.assertRaises(ModelError):
                m._chat([{"role": "user", "content": "x"}])
        self.assertEqual(calls["n"], 1, "4xx 不应重试")

    def test_5xx_is_retried(self):
        m = bare_model()
        calls = {"n": 0}

        def unstable(messages):
            calls["n"] += 1
            if calls["n"] == 1:
                raise urllib.error.HTTPError("http://x", 503, "unavailable", None, None)
            return '{"action": "search", "args": {"query": "q"}, "decision_basis": "b"}'

        m._post = unstable
        with mock.patch("src.llm.deepseek_client.time.sleep"):
            out = m._chat([{"role": "user", "content": "x"}])
        self.assertEqual(calls["n"], 2)
        self.assertIn("search", out)

    def test_error_message_never_contains_key(self):
        m = bare_model()

        def boom(messages):
            raise urllib.error.URLError("down")

        m._post = boom
        with mock.patch("src.llm.deepseek_client.time.sleep"):
            try:
                m._chat([{"role": "user", "content": "x"}])
            except ModelError as exc:
                self.assertNotIn("fake-key-should-never-leak", str(exc))
            else:
                self.fail("应当抛出 ModelError")


class TestParsing(unittest.TestCase):
    def test_plain_json(self):
        d = _parse_decision('{"action": "finish", "args": {}, "decision_basis": "b"}')
        self.assertEqual(d.action, "finish")

    def test_code_fence_is_tolerated(self):
        d = _parse_decision('```json\n{"action": "estimate", "args": {}}\n```')
        self.assertEqual(d.action, "estimate")

    def test_garbage_returns_none(self):
        self.assertIsNone(_parse_decision("我不是 JSON"))
        self.assertIsNone(_parse_decision(""))
        self.assertIsNone(_parse_decision('{"no_action": 1}'))

    def test_unknown_action_survives_parsing_but_policy_rejects(self):
        """未知动作要被解析出来交给 policy 拒绝，而不是在这里被静默吞掉。"""
        d = _parse_decision('{"action": "drop_table", "args": {}}')
        self.assertEqual(d.action, "drop_table")


if __name__ == "__main__":
    unittest.main()
