"""动作策护栏。

它不生成内容、不评价脚本质量，只回答一件事：
"这个动作在当前状态下允许被执行吗？"
所有顺序性约束集中在这里，而不是散落在循环里。
"""

from __future__ import annotations

import json
from typing import Dict, List, Tuple

from ..domain.schemas import ActionDecision, LoopState

ACTIONS: Tuple[str, ...] = (
    "search",
    "rewrite_query",
    "ask_clarify",
    "compose",
    "estimate",
    "compress",
    "check_rules",
    "finish",
)

TERMINAL_FINISH_ONLY = "修正次数已达上限，请用 finish 明确报告无法满足"


class Policy:
    def __init__(
        self,
        max_steps: int = 16,
        max_tool_calls: int = 18,
        dup_limit: int = 2,
        max_fixes: int = 3,
    ) -> None:
        self.max_steps = max_steps
        self.max_tool_calls = max_tool_calls
        self.dup_limit = dup_limit
        self.max_fixes = max_fixes

    def validate(self, decision: ActionDecision, state: LoopState) -> Tuple[bool, str]:
        action = decision.action
        args: Dict = decision.args or {}

        if action not in ACTIONS:
            return False, "动作不在白名单内：{a}".format(a=action)

        if state.tool_calls_used >= self.max_tool_calls and action != "finish":
            return False, "工具调用次数已达上限({n})，只能 finish".format(n=self.max_tool_calls)

        # 重复动作检测：同一 (action, args) 反复出现说明在原地打转。
        # estimate / check_rules 额外带上草案版本号，避免“改稿后再次估算”被误判为重复。
        sig = action + "|" + json.dumps(args, ensure_ascii=False, sort_keys=True)
        if action in ("estimate", "check_rules", "compress"):
            sig += "|rev={n}".format(n=state.revision)
        if state.duplicates.get(sig, 0) >= self.dup_limit and action not in ("finish", "ask_clarify"):
            return False, "同一动作与参数已重复 {n} 次，请换一种做法".format(n=self.dup_limit)

        if action == "compose":
            shots = args.get("shots")
            if not isinstance(shots, list) or not shots:
                return False, "compose 缺少非空的 shots"
            if state.fix_attempts >= self.max_fixes and state.last_estimate is not None and not state.last_estimate.get("passed"):
                return False, TERMINAL_FINISH_ONLY

        if action in ("estimate", "check_rules", "compress"):
            if state.draft is None:
                return False, "{a} 之前必须有脚本草案，请先 compose".format(a=action)

        if action == "finish":
            if state.draft is None:
                return False, "没有脚本草案，不能 finish"
            if not state.last_estimate or not state.last_estimate.get("passed"):
                return False, "时长估算未通过，不能 finish"
            if not state.last_rules or not state.last_rules.get("passed"):
                return False, "规则检查未通过，不能 finish"
            if not state.task.material_ids and not state.searched:
                return False, "未提供素材 ID 时须先检索素材，不能直接 finish"

        if action == "search" and not args.get("query") and not args.get("ids"):
            return False, "search 需要 query 或 ids"

        if action == "rewrite_query" and not args.get("query"):
            return False, "rewrite_query 需要新的 query"

        if action == "ask_clarify" and not args.get("question"):
            return False, "ask_clarify 需要 question"

        return True, ""
