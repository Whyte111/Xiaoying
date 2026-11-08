"""主循环：只认 Action，不做工具以外的业务判断。"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import config, guards
from .decision import DecisionPolicy
from .state import StateStore, fingerprint, scenario_digest
from .tools import Toolbox
from .types import (
    ACTION_CALL_TOOL,
    ACTION_FINISH,
    ACTION_STOP,
    STATUS_COMPLETED,
    STATUS_INCOMPLETE,
    STATUS_STOPPED_BY_LIMIT,
    STEP_FAILED,
    STEP_PENDING,
    STEP_SKIPPED,
    STEP_SUCCEEDED,
    FinalResult,
    Observation,
    PlanItem,
    TaskInput,
    ToolResult,
    Violation,
)

STEP_TEMPLATE = [
    ("s1", "检索素材", "search_material", []),
    ("s2", "生成脚本", "generate_script", ["s1"]),
    ("s3", "成本估算", "estimate_cost", ["s2"]),
    ("s4", "保存方案", "save_plan", ["s3"]),
]


class Engine:
    def __init__(
        self,
        scenario: Dict[str, Any],
        policy: DecisionPolicy,
        toolbox: Optional[Toolbox] = None,
        max_steps: int = config.MAX_STEPS,
        max_tool_calls: int = config.MAX_TOOL_CALLS,
        out_dir: Optional[Path] = None,
    ) -> None:
        self.scenario = scenario
        self.policy = policy
        self.toolbox = toolbox or Toolbox(scenario, out_dir)
        self.max_steps = max_steps
        self.max_tool_calls = max_tool_calls
        self.out_dir = Path(out_dir) if out_dir else config.OUT_DIR
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.save_fee = float(
            scenario.get("fee_table", {}).get("save_plan", {}).get("cost", 0.0)
        )
        self.scenario_hash = scenario_digest(scenario)
        self.store = StateStore(self.out_dir / "state.json")
        self.trace: List[Dict[str, Any]] = []

    def run(self, task: TaskInput) -> FinalResult:
        plan = [PlanItem(sid, name, tool, list(deps)) for sid, name, tool, deps in STEP_TEMPLATE]

        input_violations = guards.check_input(task)
        if input_violations:
            return self._blocked(
                task, plan,
                "；".join(v.detail for v in input_violations),
                input_violations,
            )

        fp = fingerprint(task, getattr(self.policy, "name", "mock"), self.scenario_hash)
        hit = self.store.get(fp)
        if hit is not None and hit.get("status") == STATUS_COMPLETED:
            reused = self._reuse(hit)
            if reused is not None:
                return reused
            self.store.delete(fp)  # 记录损坏：丢弃后按新任务执行
            hit = None

        state: Dict[str, Any] = {
            "materials": [],
            "script": None,
            "estimate": None,
            "saved": None,
            "spent": 0.0,
            "calls": 0,
            "tried_options": [],
            "results": [],
            "attempts": {},
            "violations": [],
        }

        resumed = False
        snapshot = (hit or {}).get("snapshot")
        if snapshot:
            try:
                self._restore(snapshot, state, plan)
                resumed = True
            except Exception:
                self.store.delete(fp)
        if resumed:
            self.trace.append({
                "round": 0,
                "action": "resume",
                "tool": None,
                "args": {},
                "reason": "检测到未完成任务，继承已有步骤结果继续执行",
                "note": "续跑",
            })

        status = STATUS_STOPPED_BY_LIMIT
        blocked_reason: Optional[str] = None
        rounds = 0
        last_result: Optional[ToolResult] = None

        for r in range(1, self.max_steps + 1):
            rounds = r
            state["violations"] = (
                guards.check_budget(
                    state["spent"], state["estimate"], task.budget,
                    self.save_fee, saved=state["saved"] is not None,
                )
                + guards.check_deps(plan)
                + guards.check_time(None, state["estimate"])
            )
            obs = self._observe(r, task, plan, state, last_result)

            try:
                action = self.policy.next(obs)
            except Exception as exc:
                status = STATUS_INCOMPLETE
                blocked_reason = f"决策器异常: {type(exc).__name__}: {exc}"
                self.trace.append({
                    "round": r,
                    "action": "error",
                    "tool": None,
                    "args": {},
                    "reason": blocked_reason,
                    "note": "策略异常",
                    "source": getattr(self.policy, "last_source", "mock"),
                })
                break

            if action.type == ACTION_FINISH:
                if state["saved"] is None:
                    status = STATUS_INCOMPLETE
                    blocked_reason = "决策器请求结束，但方案尚未保存"
                    self._trace(r, action, None, "拒绝结束：无产物")
                    break
                status = STATUS_COMPLETED
                self._trace(r, action, None, "结束")
                break
            if action.type == ACTION_STOP:
                status = STATUS_INCOMPLETE
                blocked_reason = action.reason
                self._trace(r, action, None, "停止")
                break
            if action.type != ACTION_CALL_TOOL:
                status = STATUS_INCOMPLETE
                blocked_reason = f"决策器返回了未知动作: {action.type}"
                self._trace(r, action, None, "未知动作")
                break

            if state["calls"] >= self.max_tool_calls:
                blocked_reason = f"达到工具调用上限 {self.max_tool_calls}，任务未完成"
                break

            result = self.toolbox.call(action.tool, action.args)
            state["calls"] += 1
            state["spent"] += result.cost
            state["results"].append(result)
            state["attempts"][result.tool] = state["attempts"].get(result.tool, 0) + 1
            last_result = result
            self._apply(result, plan, state)
            self._persist(fp, state, plan)
            self._trace(
                r, action, result,
                "工具成功" if result.ok else f"工具失败: {result.error_code}",
            )

        if status == STATUS_STOPPED_BY_LIMIT and not blocked_reason:
            blocked_reason = f"达到步数上限 {self.max_steps}，任务未完成"

        # 未完成时，没执行到的步骤标记为 skipped
        if status != STATUS_COMPLETED:
            for item in plan:
                if item.status == STEP_PENDING:
                    item.status = STEP_SKIPPED

        total_plan_cost = float(state["estimate"]["total_cost"]) if state["estimate"] else 0.0
        saved = state["saved"] is not None
        total_cost = round(state["spent"] + (total_plan_cost if saved else 0.0), 2)

        result = FinalResult(
            status=status,
            topic=guards.safe_topic(task.topic),
            budget=task.budget,
            total_cost=total_cost,
            plan_cost=total_plan_cost,
            tool_fee=round(state["spent"], 2),
            plan=plan,
            tool_results=state["results"],
            violations=state["violations"],
            saved_artifact=state["saved"],
            blocked_reason=blocked_reason,
            rounds=rounds,
            tool_calls=state["calls"],
            resumed=resumed,
            fallback_calls=getattr(self.policy, "fallback_calls", 0),
        )
        result.trace_path = config.display_path(self._write_trace())
        result.fingerprint = fp
        result.provider = getattr(self.policy, "name", "mock")
        if status == STATUS_COMPLETED:
            self.store.put(fp, {"status": status, "result": result.to_dict()})
        else:
            self._persist(fp, state, plan)
        return result

    # --- 内部 ---

    def _reuse(self, hit: Dict[str, Any]) -> Optional[FinalResult]:
        try:
            reused = FinalResult.from_dict(hit["result"])
        except Exception:
            return None
        reused.reused = True
        reused.rounds = 0
        reused.tool_calls = 0
        self.trace.append({
            "round": 0,
            "action": ACTION_FINISH,
            "tool": None,
            "args": {},
            "reason": "检测到重复执行，复用已完成结果，不再调用工具",
            "note": "复用",
        })
        reused.trace_path = config.display_path(self._write_trace())
        return reused

    def _persist(self, fp: str, state: Dict[str, Any], plan: List[PlanItem]) -> None:
        """每完成一步就落盘，供失败后续跑。"""
        self.store.put(fp, {
            "status": STATUS_INCOMPLETE,
            "snapshot": {
                "materials": state["materials"],
                "script": state["script"],
                "estimate": state["estimate"],
                "saved": state["saved"],
                "spent": round(state["spent"], 2),
                "tried_options": list(state["tried_options"]),
                "plan": [asdict(p) for p in plan],
                "results": [asdict(r) for r in state["results"]],
            },
        })

    @staticmethod
    def _restore(snapshot: Dict[str, Any], state: Dict[str, Any], plan: List[PlanItem]) -> None:
        """继承上次的成功结果；不恢复重试计数，续跑要重新给机会。"""
        state["materials"] = snapshot.get("materials", [])
        state["script"] = snapshot.get("script")
        state["estimate"] = snapshot.get("estimate")
        state["saved"] = snapshot.get("saved")
        state["spent"] = float(snapshot.get("spent", 0.0))
        state["tried_options"] = list(snapshot.get("tried_options", []))
        state["results"] = [ToolResult(**r) for r in snapshot.get("results", [])]
        restored = [PlanItem(**p) for p in snapshot.get("plan", [])]
        if len(restored) == len(plan):
            plan[:] = restored

    def _observe(
        self,
        r: int,
        task: TaskInput,
        plan: List[PlanItem],
        state: Dict[str, Any],
        last_result: Optional[ToolResult],
    ) -> Observation:
        return Observation(
            round=r,
            topic=task.topic,
            budget_total=float(task.budget or 0.0),
            spent=round(state["spent"], 2),
            time_left=float("inf"),
            materials=state["materials"],
            script=state["script"],
            estimate=state["estimate"],
            saved=state["saved"],
            plan=plan,
            tried_options=list(state["tried_options"]),
            violations=list(state["violations"]),
            attempts=dict(state["attempts"]),
            last_result=last_result,
        )

    def _apply(self, result: ToolResult, plan: List[PlanItem], state: Dict[str, Any]) -> None:
        """把工具结果写回状态，并更新步骤状态。"""
        step = next((p for p in plan if p.tool == result.tool), None)
        if step is not None:
            step.status = STEP_SUCCEEDED if result.ok else STEP_FAILED
        if not result.ok:
            return
        data = result.data
        if result.tool == "search_material":
            state["materials"] = data.get("materials", [])
        elif result.tool == "generate_script":
            state["script"] = data
        elif result.tool == "estimate_cost":
            state["estimate"] = data
            if data["option_id"] not in state["tried_options"]:
                state["tried_options"].append(data["option_id"])
        elif result.tool == "save_plan":
            state["saved"] = data

    def _trace(self, r: int, action: Any, result: Optional[ToolResult], note: str) -> None:
        entry: Dict[str, Any] = {
            "round": r,
            "action": action.type,
            "tool": action.tool,
            "args": action.args,
            "reason": action.reason,
            "note": note,
            "source": getattr(self.policy, "last_source", "mock"),
        }
        if result is not None:
            entry["ok"] = result.ok
            entry["cost"] = result.cost
            entry["summary"] = self._summarize(result)
        self.trace.append(entry)

    @staticmethod
    def _summarize(result: ToolResult) -> Dict[str, Any]:
        if not result.ok:
            return {"error_code": result.error_code, "error_msg": result.error_msg}
        data = result.data
        if result.tool == "search_material":
            return {"count": data.get("count")}
        if result.tool == "generate_script":
            return {"script_id": data.get("script_id")}
        if result.tool == "estimate_cost":
            return {"option_id": data.get("option_id"), "total_cost": data.get("total_cost")}
        if result.tool == "save_plan":
            return {"artifact_id": data.get("artifact_id")}
        return {}

    def _write_trace(self) -> Path:
        path = self.out_dir / "trace.json"
        path.write_text(json.dumps(self.trace, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def _blocked(
        self,
        task: TaskInput,
        plan: List[PlanItem],
        reason: str,
        violations: Optional[List[Violation]] = None,
    ) -> FinalResult:
        result = FinalResult(
            status=STATUS_INCOMPLETE,
            topic=guards.safe_topic(task.topic),
            budget=task.budget,
            total_cost=0.0,
            plan_cost=0.0,
            tool_fee=0.0,
            plan=plan,
            tool_results=[],
            violations=violations or [],
            blocked_reason=reason,
        )
        result.trace_path = config.display_path(self._write_trace())
        return result
