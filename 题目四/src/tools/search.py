"""素材检索工具：纯本地、确定性、对 data/ 只读、无网络。"""

from __future__ import annotations

from typing import List, Optional

from ..config import Ctx
from ..domain.errors import ErrorCode
from ..domain.schemas import ToolResult


def search(query: str, ctx: Ctx, ids: Optional[List[str]] = None) -> ToolResult:
    query = (query or "").strip()
    if ids:
        hits = [ctx.materials[i] for i in ids if i in ctx.materials]
        unknown = [i for i in ids if i not in ctx.materials]
        if unknown:
            return ToolResult(
                tool="search",
                ok=False,
                args={"ids": ids},
                digest="指定的素材 ID 不存在：{ids}".format(ids=",".join(unknown)),
                error_code=ErrorCode.TOOL_ERROR.value,
                message="unknown material ids",
            )
    else:
        q = "".join(query.lower().split())
        hits = []
        if q:
            for mid, item in ctx.materials.items():
                tags = [str(t).lower() for t in item.get("tags", [])]
                title = str(item.get("title", "")).lower()
                haystack = "".join(
                    [title, "".join(str(f).lower() for f in item.get("facts", [])), "".join(tags)]
                )
                # 命中条件：整串出现在素材文本中，或某个标签/标题出现在查询里。
                # 不做单字符模糊匹配，避免任何中文查询都误命中。
                if q in haystack or any(tag and tag in q for tag in tags) or (title and title in q):
                    hits.append(item)

    if not hits:
        return ToolResult(
            tool="search",
            ok=False,
            args={"query": query, "ids": ids or []},
            digest="检索无结果：query='{q}'".format(q=query),
            error_code=ErrorCode.SEARCH_EMPTY.value,
            message="no material matched",
        )

    flat = [
        {"id": item["id"], "title": item.get("title", ""), "facts": item.get("facts", [])}
        for item in hits
    ]
    # 观察必须携带 facts 正文：模型只有看到可用事实，才能照抄 fact_refs，
    # 否则它只能反复检索或凭空编造。
    parts = []
    for item in flat:
        facts = " / ".join(item["facts"]) if item["facts"] else "无事实"
        parts.append("{i}（{f}）".format(i=item["id"], f=facts))
    digest = "命中 {n} 条素材：{p}".format(n=len(flat), p="；".join(parts))
    return ToolResult(
        tool="search",
        ok=True,
        args={"query": query, "ids": ids or []},
        digest=digest,
        data={"hits": flat, "count": len(flat)},
    )
