"""工具层：search / read_doc / ask_user / finish。异常一律转成 ToolResult(ok=False)。"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import conflict as conflict_mod
from .corpus import Corpus
from .retrieval import Retriever, tokenize


@dataclass
class ToolResult:
    tool: str
    args: "Dict[str, Any]"
    ok: bool
    payload: "Any"
    summary: str
    error_code: "Optional[str]" = None
    fatal: bool = False          # 只有系统级异常才算故障；业务性失败（找不到/参数错）不算
    elapsed_ms: int = 0
    conflicts: "List[conflict_mod.Conflict]" = field(default_factory=list)


class Toolbox:
    NAMES = ("search", "read_doc", "ask_user", "finish")

    def __init__(self, retriever: Retriever, corpus: Corpus, top_k: int = 4) -> None:
        self.retriever = retriever
        self.corpus = corpus
        self.top_k = top_k
        self.question = ""  # 由 Agent 每轮写入，用于过滤与问题无关的冲突提示

    def describe(self) -> str:
        return (
            "- search(query, k): 关键词检索制度文档，返回若干片段（doc_id / 版本 / 小节 / 原文摘要）\n"
            "- read_doc(doc_id): 读取指定文档的全部小节原文\n"
            "- ask_user(question): 向用户追问；当检索结果存在冲突或信息不足以判断时使用\n"
            "- finish(answer, citations, gaps): 结束并给出最终答复；"
            "citations 为 [{doc_id, quote}]，quote 必须逐字来自原文"
        )

    def run(self, action: str, args: "Dict[str, Any]") -> ToolResult:
        t0 = time.perf_counter()
        try:
            if action == "search":
                res = self._search(args)
            elif action == "read_doc":
                res = self._read_doc(args)
            elif action == "ask_user":
                res = self._ask_user(args)
            elif action == "finish":
                res = ToolResult("finish", args, True, args, "finish")
            else:
                res = ToolResult(action, args, False, None,
                                 f"未知工具 {action}", "UNKNOWN_TOOL")
        except Exception as e:  # 兜底，绝不让异常冒到循环
            res = ToolResult(action, args, False, None,
                             f"{type(e).__name__}: {e}", "TOOL_EXCEPTION", fatal=True)
        res.elapsed_ms = int((time.perf_counter() - t0) * 1000)
        return res

    def _search(self, args: "Dict[str, Any]") -> ToolResult:
        query = str(args.get("query", "")).strip()
        if not query:
            return ToolResult("search", args, False, None, "query 为空", "BAD_ARGS")
        try:
            k = int(args.get("k") or self.top_k)
        except (TypeError, ValueError):
            k = self.top_k
        k = max(1, min(k, 10))  # 模型可传任意 k，必须夹紧
        hits = self.retriever.search(query, k)
        if not hits:
            return ToolResult("search", args, True, [], "无命中结果")

        lines, payload = [], []
        for h in hits:
            seg = h.segment
            lines.append(f"[{seg.doc_id} v{seg.version_date} {seg.section_id} "
                         f"{seg.section_title}] {h.snippet}")
            payload.append({
                "doc_id": seg.doc_id, "version_date": seg.version_date,
                "section_id": seg.section_id, "text": seg.text, "score": h.score,
            })

        found = conflict_mod.detect([h.segment for h in hits])
        found = self._relevant_conflicts(found)
        if found:
            lines.append("")
            lines.append("⚠ 检测到制度冲突（不同文档对同一主题给出不同数值）：")
            for c in found:
                lines.append("  " + c.describe())
            lines.append("  处理要求：不得自行选择其中一个，应调用 ask_user 请求确认适用制度。")

        return ToolResult("search", args, True, payload, "\n".join(lines), conflicts=found)

    def _relevant_conflicts(self, found: "List[conflict_mod.Conflict]") -> "List[conflict_mod.Conflict]":
        """只提示与用户问题相关的冲突；否则宽泛检索会把无关条款的冲突也报出来。"""
        if not self.question:
            return found
        q = set(tokenize(self.question))
        return [c for c in found if q & set(tokenize(c.topic))]

    def _read_doc(self, args: "Dict[str, Any]") -> ToolResult:
        doc_id = str(args.get("doc_id", "")).strip()
        segs = self.retriever.read(doc_id)
        if not segs:
            return ToolResult("read_doc", args, False, None,
                              f"文档 {doc_id} 不存在", "NOT_FOUND")
        text = "\n".join(f"## {s.section_title}\n{s.text}" for s in segs)
        return ToolResult("read_doc", args, True,
                          [{"section_id": s.section_id, "text": s.text} for s in segs],
                          text[:1200])

    def _ask_user(self, args: "Dict[str, Any]") -> ToolResult:
        question = str(args.get("question", "")).strip()
        if not question:
            return ToolResult("ask_user", args, False, None, "question 为空", "BAD_ARGS")
        return ToolResult("ask_user", args, True, {"question": question},
                          f"向用户追问：{question}")
