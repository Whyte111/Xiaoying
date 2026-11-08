"""结果组装与引用校验：引用必须在原文中逐字存在（忽略空白差异），否则剔除并降级。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .corpus import Corpus
from .retrieval import tokenize

# 引用相关性是"弱启发式"，不是语义蕴含：
#   cov_quote = 引用里有多少词出现在回答中；cov_answer = 回答里有多少词出现在引用中。
# 阈值来自 4 组样本实测（无关引用 0.08/0.17，最弱的合法引用 0.18/0.50）。
RELEVANCE_MIN_QUOTE_COVER = 0.15
RELEVANCE_MIN_ANSWER_COVER = 0.30


def _coverage(quote: str, answer_text: str) -> "tuple[float, float]":
    qt = set(tokenize(quote))
    at = set(tokenize(answer_text))
    if not qt or not at:
        return 0.0, 0.0
    inter = len(qt & at)
    return inter / len(qt), inter / len(at)

ANSWERED = "ANSWERED"
NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
INSUFFICIENT = "INSUFFICIENT"
TOOL_ERROR = "TOOL_ERROR"
STEP_LIMIT = "STEP_LIMIT"

_STATUS_ORDER = (TOOL_ERROR, STEP_LIMIT, NEEDS_CLARIFICATION, INSUFFICIENT, ANSWERED)


@dataclass
class Citation:
    doc_id: str
    version_date: str
    section: str
    quote: str
    verified: bool


@dataclass
class Answer:
    status: str
    text: str
    citations: "List[Citation]" = field(default_factory=list)
    gaps: "List[str]" = field(default_factory=list)
    question: "Optional[str]" = None


def normalize(text: str) -> str:
    """中文场景下空白不承载语义，校验时统一去掉；其余字符保持逐字。"""
    return re.sub(r"\s+", "", text or "")


def _as_list(value: "Any") -> "List[Any]":
    """模型输出形状会漂移：单个字符串、单个对象都可能出现，这里统一成列表。"""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _supports(quote: str, answer_text: str, question: str = "") -> bool:
    """引用是否与问答沾边。挡得住"原文真实但与问题无关"，挡不住精细的断章取义。"""
    qt = set(tokenize(quote))
    if not qt:
        return False
    if not set(tokenize(answer_text)):
        return True  # 没有回答文本时无法判断，交给后面的空答案逻辑
    cov_q, cov_a = _coverage(quote, answer_text)
    if cov_q >= RELEVANCE_MIN_QUOTE_COVER or cov_a >= RELEVANCE_MIN_ANSWER_COVER:
        return True
    # 回退：模型常把答案改写成口语，不与原文复用措辞；此时看引用是否与问题有交集。
    # 要求至少 2 个共同词，否则「员工」这类通用词会把无关引用放进来。
    if not question:
        return False
    return len(qt & set(tokenize(question))) >= 2


def assemble(args: "Dict[str, Any]", corpus: Corpus, question: str = "") -> Answer:
    text = str(args.get("answer", "")).strip()
    gaps = [str(g) for g in _as_list(args.get("gaps")) if str(g).strip()]
    raw = _as_list(args.get("citations"))

    kept: "List[Citation]" = []
    dropped = 0
    irrelevant = 0
    for item in raw:
        if not isinstance(item, dict):
            dropped += 1
            continue
        doc_id = str(item.get("doc_id", "")).strip()
        quote = str(item.get("quote", "")).strip()
        doc = corpus.doc(doc_id)
        ok = bool(doc) and bool(quote) and normalize(quote) in normalize(doc.body)
        if not ok:
            dropped += 1
            continue
        if not _supports(quote, text, question):
            irrelevant += 1
            continue
        section = next((s.section_title for s in doc.segments
                        if normalize(quote) in normalize(s.text)), "")
        kept.append(Citation(doc_id, doc.version_date, section, quote, True))

    if dropped:
        gaps.append(f"{dropped} 条引用无法在原文中逐字校验，已剔除")
    if irrelevant:
        gaps.append(f"{irrelevant} 条引用与回答内容无关，已剔除")

    if not kept:
        if not gaps:
            gaps.append("没有可核验的原文引用，无法给出有依据的答案")
        return Answer(INSUFFICIENT, text or "资料不足，无法回答该问题。", kept, gaps)

    if not text:
        text = "已找到相关制度条款，详见引用。"
    return Answer(ANSWERED, text, kept, gaps)
