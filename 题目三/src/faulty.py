"""故障注入：仅用于验证"工具失败"路径，默认关闭，不影响正常流程。"""
from __future__ import annotations

from typing import List

from .corpus import DocSegment
from .retrieval import Retriever, SearchHit


class FaultyRetriever:
    """包装真实检索器，前 N 次调用抛异常，之后恢复正常。"""

    def __init__(self, inner: Retriever, times: int = 1) -> None:
        self.inner = inner
        self.remaining = times
        self.triggered = 0

    def search(self, query: str, k: int = 4) -> "List[SearchHit]":
        if self.remaining > 0:
            self.remaining -= 1
            self.triggered += 1
            raise RuntimeError("注入故障：检索服务不可用")
        return self.inner.search(query, k)

    def read(self, doc_id: str) -> "List[DocSegment]":
        return self.inner.read(doc_id)
