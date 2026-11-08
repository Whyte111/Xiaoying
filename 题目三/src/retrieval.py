"""分词 + BM25 检索。零第三方依赖：jieba 可用则用，否则退化为字符二元组。"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from .corpus import Corpus, DocSegment

try:
    import jieba  # type: ignore

    jieba.setLogLevel(60)
except Exception:  # pragma: no cover - 环境无关的降级路径
    jieba = None

_ALNUM = re.compile(r"[a-z0-9]+")
_CJK = re.compile(r"[\u4e00-\u9fff]+")


def tokenize(text: str) -> "list[str]":
    low = text.lower()
    tokens = _ALNUM.findall(low)
    for run in _CJK.findall(low):
        if jieba is not None:
            tokens.extend(w for w in jieba.lcut(run) if len(w) >= 2)
        else:
            tokens.extend(run[i:i + 2] for i in range(len(run) - 1))
    return tokens


@dataclass
class SearchHit:
    segment: DocSegment
    score: float
    snippet: str


class _BM25:
    def __init__(self, docs: "list[list[str]]", k1: float = 1.5, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self.n = len(docs)
        self.avgdl = sum(len(d) for d in docs) / self.n if self.n else 0.0
        self.tfs = [Counter(d) for d in docs]
        df: "Counter[str]" = Counter()
        for d in docs:
            df.update(set(d))
        self.idf = {
            t: math.log(1 + (self.n - n + 0.5) / (n + 0.5)) for t, n in df.items()
        }

    def score(self, query: "list[str]", i: int) -> float:
        tf, dl, total = self.tfs[i], sum(self.tfs[i].values()), 0.0
        for t in query:
            f = tf.get(t)
            if not f:
                continue
            total += self.idf.get(t, 0.0) * f * (self.k1 + 1) / (
                f + self.k1 * (1 - self.b + self.b * dl / (self.avgdl or 1))
            )
        return total


class Retriever:
    def __init__(self, corpus: Corpus) -> None:
        self.corpus = corpus
        self._tokens = [tokenize(s.section_title + " " + s.text) for s in corpus.segments]
        self._bm25 = _BM25(self._tokens)

    def search(self, query: str, k: int = 4) -> "list[SearchHit]":
        q = tokenize(query)
        scored = [(self._bm25.score(q, i), i) for i in range(len(self.corpus.segments))]
        scored = [x for x in scored if x[0] > 0]
        scored.sort(reverse=True)
        hits = []
        for score, i in scored[:k]:
            seg = self.corpus.segments[i]
            hits.append(SearchHit(seg, round(score, 4), seg.text[:160]))
        return hits

    def read(self, doc_id: str) -> "list[DocSegment]":
        doc = self.corpus.doc(doc_id)
        return doc.segments if doc else []
