"""会话状态：保存多轮上下文，供追问时的指代消解使用。单进程内存态，不持久化。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from .answer import Answer


@dataclass
class Turn:
    index: int
    query: str
    status: str
    answer: str
    doc_ids: "List[str]" = field(default_factory=list)
    question: "Optional[str]" = None


class Session:
    def __init__(self) -> None:
        self.turns: "List[Turn]" = []

    def add(self, query: str, answer: Answer) -> None:
        self.turns.append(Turn(
            index=len(self.turns) + 1,
            query=query,
            status=answer.status,
            answer=answer.text,
            doc_ids=[c.doc_id for c in answer.citations],
            question=answer.question,
        ))

    def context(self, limit: int = 2) -> str:
        recent = self.turns[-limit:] if limit else []
        if not recent:
            return ""
        lines = []
        for t in recent:
            if t.status == "NEEDS_CLARIFICATION" and t.question:
                lines.append(f"第{t.index}轮 - 你向用户追问过：{t.question}")
            else:
                docs = ",".join(dict.fromkeys(t.doc_ids)) or "无"
                lines.append(f"第{t.index}轮 - 问题：{t.query}；结论：{t.answer[:80]}；引用文档：{docs}")
        return "\n".join(lines)

    def __len__(self) -> int:
        return len(self.turns)
