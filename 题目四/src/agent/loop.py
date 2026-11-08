"""主循环：模型选动作 → 护栏校验 → 执行工具 → 观察回灌 → 再决策。

边界：
- 循环自己写 trace，不依赖模型；
- 所有终止路径都在这里收敛为显式终态，不静默截断。
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from ..config import Ctx
from ..domain.errors import ErrorCode, Status
from ..domain.schemas import (
    ActionDecision,
    LoopState,
    Observation,
    RunResult,
    TaskInput,
    ToolResult,
)
from ..llm.base import ModelClient, ModelError
from ..observability.trace import TraceWriter
from .dispatcher import Dispatcher
from .policy import Policy


class Loop:
    def __init__(
        self,
        model: ModelClient,
        ctx: Ctx,
        policy: Optional[Policy] = None,
        trace: Optional[TraceWriter] = None,
    ) -> None:
        self.model = model
        self.ctx = ctx
        self.policy = policy or Policy()
        self.trace = trace or TraceWriter(None)
        self.dispatcher = Dispatcher(ctx)

    def run(self, task: TaskInput) -> RunResult:
        self.trace.emit(
            {
                "event": "run_start",
                "model": getattr(self.model, "name", "unknown"),
                "task": task.to_dict(),
                "limits": {
                    "max_steps": self.policy.max_steps,
                    "max_tool_calls": self.policy.max_tool_calls,
                    "max_fixes": self.policy.max_fixes,
                },
            }
        )

        problems = task.problems()
        if problems:
            reason = "输入不合法：{m}".format(m="；".join(problems))
            return self._terminal(
                None, Status.NEEDS_INPUT, reason, ErrorCode.MISSING_INPUT, partial=None
            )

        state = LoopState(task=task)

        while True:
            if state.steps_used >= self.policy.max_steps:
                return self._terminal(
                    state,
                    Status.INCOMPLETE,
                    "达到步数上限 {n}，任务未完成".format(n=self.policy.max_steps),
                    ErrorCode.STEP_LIMIT,
                )

            state.steps_used += 1

            try:
                decision = self.model.decide(state)
            except ModelError as exc:
                return self._terminal(
                    state, Status.FAILED, "模型调用失败：{e}".format(e=exc), ErrorCode.MODEL_ERROR
                )

            ok, reason = self.policy.validate(decision, state)
            self._count_duplicate(decision, state)
            self.trace.emit(
                {
                    "event": "decision",
                    "step": state.steps_used,
                    "action": decision.action,
                    "args": decision.args,
                    "decision_basis": decision.decision_basis,
                    "accepted": ok,
                    "reject_reason": "" if ok else reason,
                }
            )

            if not ok:
                state.observations.append(
                    Observation(
                        step=state.steps_used,
                        action=decision.action,
                        args=decision.args,
                        result_digest="动作被护栏驳回：{r}".format(r=reason),
                        accepted=False,
                        note=reason,
                    )
                )
                continue

            if decision.action == "ask_clarify":
                question = str(decision.args.get("question") or "请补充信息")
                code = (
                    ErrorCode.SEARCH_EMPTY
                    if not state.search_hit
                    else ErrorCode.MISSING_INPUT
                )
                return self._terminal(state, Status.NEEDS_INPUT, question, code)

            if decision.action == "finish":
                return self._completed(state)

            result = self.dispatcher.dispatch(decision, state)
            state.tool_calls_used += 1
            self.trace.emit(
                {
                    "event": "tool_result",
                    "step": state.steps_used,
                    "tool": result.tool,
                    "ok": result.ok,
                    "error_code": result.error_code,
                    "digest": result.digest,
                    "data": self._brief_data(result),
                }
            )
            state.observations.append(
                Observation(
                    step=state.steps_used,
                    action=decision.action,
                    args=decision.args,
                    result_digest=result.digest,
                    accepted=result.ok,
                    note=result.error_code or "",
                )
            )

            # 工具已判定“不可能满足”时立即收敛，不要空转到步数上限
            if result.error_code == ErrorCode.UNSATISFIABLE.value:
                return self._terminal(
                    state,
                    Status.INCOMPLETE,
                    "工具判定无法满足：{d}".format(d=result.digest),
                    ErrorCode.UNSATISFIABLE,
                )

            # B3 硬闸：修正次数用尽仍超时长，必须明确失败，不能继续磨
            if (
                decision.action == "estimate"
                and state.last_estimate is not None
                and not state.last_estimate.get("passed")
                and state.fix_attempts >= self.policy.max_fixes
            ):
                return self._terminal(
                    state,
                    Status.INCOMPLETE,
                    "已修正 {n} 次，总时长仍超出目标 {t}s".format(
                        n=state.fix_attempts, t=state.last_estimate.get("target_s")
                    ),
                    ErrorCode.UNSATISFIABLE,
                )

    # ---- 终态组装 ----
    def _completed(self, state: LoopState) -> RunResult:
        draft = state.draft
        material_refs = sorted({m for s in draft.shots for m in s.material_ids})
        script = draft.to_dict()
        script["estimated_total_s"] = draft.total_duration()
        script["material_refs"] = material_refs
        script["target_duration_s"] = state.task.target_duration_s
        script["status"] = Status.COMPLETED.value
        return self._terminal(
            state,
            Status.COMPLETED,
            "时长与规则检查均通过",
            None,
            script=script,
            partial=None,
        )

    def _terminal(
        self,
        state: Optional[LoopState],
        status: Status,
        reason: str,
        error_code: Optional[ErrorCode],
        script: Optional[Dict[str, Any]] = None,
        partial: Optional[Dict[str, Any]] = None,
    ) -> RunResult:
        if state is not None and partial is None and state.draft is not None and script is None:
            partial = state.draft.to_dict()
            partial["estimated_total_s"] = state.last_estimate.get("total_s") if state.last_estimate else None

        event = {
            "event": "terminal",
            "status": status.value,
            "reason": reason,
            "error_code": error_code.value if error_code else None,
            "steps_used": state.steps_used if state else 0,
            "tool_calls_used": state.tool_calls_used if state else 0,
        }
        self.trace.emit(event)

        return RunResult(
            status=status.value,
            reason=reason,
            error_code=error_code.value if error_code else None,
            script=script,
            steps_used=state.steps_used if state else 0,
            tool_calls_used=state.tool_calls_used if state else 0,
            partial=partial,
            trace_path=str(self.trace.path) if self.trace.path else "",
        )

    # ---- 工具 ----
    @staticmethod
    def _count_duplicate(decision: ActionDecision, state: LoopState) -> None:
        key = decision.action + "|" + json.dumps(decision.args or {}, sort_keys=True, ensure_ascii=False)
        if decision.action in ("estimate", "check_rules", "compress"):
            key += "|rev={n}".format(n=state.revision)
        state.duplicates[key] = state.duplicates.get(key, 0) + 1

    @staticmethod
    def _brief_data(result: ToolResult) -> Dict[str, Any]:
        """trace 里只放摘要，不放全量数据，避免文件膨胀。"""
        data = result.data or {}
        brief: Dict[str, Any] = {}
        for key in ("total_s", "target_s", "passed", "count", "shots", "narration_chars"):
            if key in data:
                brief[key] = data[key]
        if "hits" in data:
            brief["hit_ids"] = [h.get("id") for h in data["hits"]]
        if "violations" in data:
            brief["violations"] = [
                v.get("code") for v in data["violations"]
            ]
        return brief
