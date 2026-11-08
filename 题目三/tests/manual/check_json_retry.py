"""手工校验：模型第一次返回非法 JSON 时，Agent 应自动重试一次而不是直接 TOOL_ERROR。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.agent import Agent
from src.config import Config
from src.corpus import Corpus
from src.llm import LLMError
from src.retrieval import Retriever


class FlakyLLM:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, messages):
        self.calls += 1
        if self.calls == 1:
            raise LLMError("模型未返回合法 JSON: <html>502 Bad Gateway</html>")
        return {
            "action": "finish",
            "args": {
                "answer": "一线城市每人每晚 600 元。",
                "citations": [{
                    "doc_id": "D01",
                    "quote": "一线城市住宿标准为每人每晚 600 元，二线城市为每人每晚 400 元，超出标准部分由个人承担。",
                }],
                "gaps": [],
            },
            "reason": "重试后给出答案",
        }


def main() -> int:
    cfg = Config.load()
    corpus = Corpus.load(Path("data"))
    llm = FlakyLLM()
    ans = Agent(cfg, llm, corpus, Retriever(corpus), None).run("住宿标准")
    print(f"状态={ans.status} 调用次数={llm.calls}")
    ok = ans.status == "ANSWERED" and llm.calls == 2
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
