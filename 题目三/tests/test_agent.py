"""端到端（离线回放）测试：主链路 + 关键异常路径。全部不依赖网络。"""
from __future__ import annotations

import unittest
from pathlib import Path

from src.agent import Agent
from src.config import Config
from src.corpus import Corpus
from src.faulty import FaultyRetriever
from src.llm import FakeLLM, LLMError
from src.retrieval import Retriever
from src.session import Session

QUOTE = "一线城市住宿标准为每人每晚 600 元，二线城市为每人每晚 400 元，超出标准部分由个人承担。"
Q_REIMBURSE = "报销申请须在费用发生后 15 个工作日内提交，逾期原则上不予受理。"


def finish(answer: str, citations=None, gaps=None) -> dict:
    return {"action": "finish", "reason": "done",
            "args": {"answer": answer, "citations": citations or [], "gaps": gaps or []}}


def search(query: str) -> dict:
    return {"action": "search", "reason": "查", "args": {"query": query}}


class AgentCase(unittest.TestCase):
    def run_agent(self, script, steps: int = 6, retriever=None,
                  session: Session = None, turn: int = 1, question: str = "测试问题"):
        cfg = Config.load()
        cfg.max_steps = steps
        corpus = Corpus.load(Path("data"))
        retriever = retriever or Retriever(corpus)
        return Agent(cfg, FakeLLM(script), corpus, retriever, None).run(
            question, session, turn)


class TestMainPath(AgentCase):
    def test_happy_path(self):
        ans = self.run_agent([search("住宿标准"),
                              finish("一线城市每人每晚 600 元。",
                                     [{"doc_id": "D01", "quote": QUOTE}])])
        self.assertEqual(ans.status, "ANSWERED")
        self.assertEqual(len(ans.citations), 1)
        self.assertTrue(ans.citations[0].verified)

    def test_conflict_gate_forces_clarification(self):
        """模型只引 D02 直接给 15 天 —— 必须被门禁拦下。"""
        ans = self.run_agent([search("报销期限"),
                              finish("15 个工作日。", [{"doc_id": "D02", "quote": Q_REIMBURSE}])],
                             question="报销期限是多久？")
        self.assertEqual(ans.status, "NEEDS_CLARIFICATION")
        self.assertEqual(len(ans.citations), 2)
        self.assertIn("冲突", ans.text)

    def test_conflict_without_citation_still_clarifies(self):
        ans = self.run_agent([search("报销期限"), finish("15 个工作日。")],
                             question="报销期限是多久？")
        self.assertEqual(ans.status, "NEEDS_CLARIFICATION")

    def test_out_of_scope(self):
        ans = self.run_agent([search("年会"), finish("没有相关规定。", gaps=["无资料"])])
        self.assertEqual(ans.status, "INSUFFICIENT")

    def test_multi_turn_keeps_context(self):
        session = Session()
        cfg = Config.load()
        corpus = Corpus.load(Path("data"))
        retriever = Retriever(corpus)
        a1 = Agent(cfg, FakeLLM([search("年假"), finish("7 天。", [{
            "doc_id": "D03",
            "quote": "入职满 1 年不满 3 年的员工，年假 5 天；满 3 年不满 5 年的，年假 7 天；满 5 年以上的，年假 10 天。"}])]),
            corpus, retriever, None).run("年假几天", session, 1)
        session.add("年假几天", a1)
        self.assertIn("年假", session.context())
        self.assertEqual(a1.status, "ANSWERED")


class TestExceptionPaths(AgentCase):
    def test_step_limit(self):
        ans = self.run_agent([search("住宿标准")], steps=1)
        self.assertEqual(ans.status, "STEP_LIMIT")

    def test_tool_failure_recovers(self):
        cfg = Config.load()
        corpus = Corpus.load(Path("data"))
        retriever = FaultyRetriever(Retriever(corpus), times=1)
        ans = self.run_agent([search("住宿标准"), search("住宿 标准"),
                              finish("600 元。", [{"doc_id": "D01", "quote": QUOTE}])],
                             retriever=retriever)
        self.assertEqual(ans.status, "ANSWERED")

    def test_consecutive_tool_failure_aborts(self):
        cfg = Config.load()
        corpus = Corpus.load(Path("data"))
        retriever = FaultyRetriever(Retriever(corpus), times=9)
        ans = self.run_agent([search("a"), search("b"), search("c")], retriever=retriever)
        self.assertEqual(ans.status, "TOOL_ERROR")

    def test_business_failure_is_not_tool_error(self):
        """连续读到不存在的文档是业务失败，不该把整轮判死。"""
        bad = {"action": "read_doc", "reason": "x", "args": {"doc_id": "D99"}}
        ans = self.run_agent([bad, dict(bad), dict(bad), finish("查不到。", gaps=["无"])])
        self.assertEqual(ans.status, "INSUFFICIENT")

    def test_llm_json_error_retried_once(self):
        class Flaky:
            calls = 0

            def complete(self, messages):
                Flaky.calls += 1
                if Flaky.calls == 1:
                    raise LLMError("坏 JSON")
                return finish("600 元。", [{"doc_id": "D01", "quote": QUOTE}])

        cfg = Config.load()
        corpus = Corpus.load(Path("data"))
        ans = Agent(cfg, Flaky(), corpus, Retriever(corpus), None).run("住宿标准")
        self.assertEqual(ans.status, "ANSWERED")
        self.assertEqual(Flaky.calls, 2)


if __name__ == "__main__":
    unittest.main()
