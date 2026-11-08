"""Agent 循环：取动作 → 校验 → 执行工具 → 记录 → 判终止或上限。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, List, Set, Tuple

from ..domain.gating import allowed_actions
from ..domain.models import (
    Action,
    AuditStatus,
    Draft,
    DraftStatus,
    Facts,
    ItemResult,
    PendingItem,
    RunResult,
    RunStatus,
    Severity,
    StepRecord,
    TOOL_NAMES,
    ToolResult,
)
from ..tools.toolkit import Toolbox
from ..trace import TraceWriter
from .policy import pick


@dataclass
class Limits:
    max_steps: int = 8
    max_tool_calls: int = 8


Decider = Callable[[Facts, Set[Action]], Action]

_SAVE_KIND_TO_STATUS = {
    "created": DraftStatus.SAVED_CREATED,
    "idempotent": DraftStatus.SAVED_IDEMPOTENT,
    "updated": DraftStatus.SAVED_UPDATED,
    "failed": DraftStatus.FAILED,
}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _add_pending(facts: Facts, item: PendingItem) -> None:
    if all(p.question != item.question for p in facts.pending):
        facts.pending.append(item)


def _audit_status(facts: Facts) -> AuditStatus:
    if facts.fetch_failed or facts.material is None or not facts.material_complete:
        return AuditStatus.NEED_INFO
    if facts.has_block or facts.blocked:
        return AuditStatus.BLOCKED
    if any(f.severity is Severity.WARN for f in facts.findings):
        return AuditStatus.WARN
    return AuditStatus.PASS


def _build_draft(facts: Facts, material_id: str) -> Draft:
    material = facts.material
    suggested = facts.candidate or (material.requested_slot if material else None)
    pending = list(facts.pending)
    # 不变量：只要建议时段与原要求不同，就必须带一条待确认，无论哪条代码路径改的
    if (
        material is not None
        and suggested is not None
        and material.requested_slot is not None
        and suggested != material.requested_slot
        and not any(p.kind == "slot_change" for p in pending)
    ):
        pending.append(
            PendingItem(
                "slot_change",
                "建议时段 %s 与原要求 %s 不同，需用户确认"
                % (suggested.label(), material.requested_slot.label()),
            )
        )
    for finding in facts.findings:
        if finding.severity is Severity.WARN:
            item = PendingItem("risk_warn", "规则 %s：%s，是否确认接受？" % (finding.rule_id, finding.msg))
            if all(p.question != item.question for p in pending):
                pending.append(item)
    stamp = _now()
    return Draft(
        material_id=material_id,
        audit_status=_audit_status(facts),
        suggested_slot=suggested,
        pending=pending,
        risk=list(facts.findings),
        created_at=stamp,
        updated_at=stamp,
    )


def _execute(
    action: Action, facts: Facts, toolbox: Toolbox, material_id: str
) -> Tuple[ToolResult, dict, str, str]:
    if action is Action.FETCH:
        args = {"material_id": material_id}
        result = toolbox.read_material(material_id)
        if result.ok:
            material = result.data
            facts.material = material
            missing = material.missing_fields()
            facts.material_complete = not missing
            if missing:
                summary = "读取成功；缺少字段：%s" % "、".join(missing)
                rationale = "素材不完整 → 只能请求补查或结束"
            else:
                summary = "读取成功；标题=%s" % material.title
                rationale = "素材完整 → 下一步检查规则风险"
        else:
            facts.fetch_failed = True
            summary = "读取失败（%s）：%s" % (result.code, result.err)
            rationale = "素材不存在或不可读 → 无法继续"
        return result, args, summary, rationale

    if action is Action.CHECK:
        args = {"material_id": facts.material.id}
        result = toolbox.check_rules(facts.material)
        if result.ok:
            facts.rules_checked = True
            facts.findings = list(result.data)
            blocks = [f for f in facts.findings if f.severity is Severity.BLOCK]
            warns = [f for f in facts.findings if f.severity is Severity.WARN]
            summary = "命中 %d 条风险（block=%d, warn=%d）" % (len(facts.findings), len(blocks), len(warns))
            if blocks:
                rationale = "存在 block 风险 → 阻断保存"
            elif warns:
                rationale = "仅有 warn 风险 → 可保存但需列待确认"
            else:
                rationale = "无风险 → 继续检查时段"
        else:
            facts.tool_error = True
            facts.tool_error_detail = "check_rules: %s" % result.err
            summary = "规则检查失败：%s" % result.err
            rationale = "工具故障 → 不能下结论，本轮中止（不得伪装成到上限）"
        return result, args, summary, rationale

    if action is Action.QUERY:
        slot = facts.material.requested_slot
        args = {"slot": slot.label()}
        result = toolbox.query_slots(slot)
        if result.ok:
            facts.conflicts_checked = True
            facts.conflicts = list(result.data)
            summary = "时段 %s 冲突 %d 处" % (slot.label(), len(facts.conflicts))
            rationale = "存在冲突 → 生成替代时段" if facts.conflicts else "无冲突 → 可直接保存草稿"
        else:
            facts.tool_error = True
            facts.tool_error_detail = "query_slots: %s" % result.err
            summary = "时段查询失败：%s" % result.err
            rationale = "工具故障 → 无法判断冲突，本轮中止"
        return result, args, summary, rationale

    if action is Action.RESCHEDULE:
        requested = facts.material.requested_slot
        args = {"requested": requested.label()}
        try:
            candidates = toolbox.suggest(requested)
        except Exception as exc:  # 时段计算故障不得让循环崩掉
            facts.tool_error = True
            facts.tool_error_detail = "suggest: %s" % exc
            return (
                ToolResult.error("INVALID", str(exc)),
                args,
                "生成候选时段失败：%s" % exc,
                "时段计算故障 → 中止，不硬排",
            )
        if candidates:
            facts.candidate = candidates[0]
            _add_pending(
                facts,
                PendingItem(
                    "slot_change",
                    "原时段 %s 已被占用，是否接受改到 %s？" % (requested.label(), candidates[0].label()),
                ),
            )
            result = ToolResult(ok=True, data=candidates)
            summary = "候选 %d 个：%s" % (len(candidates), "、".join(c.label() for c in candidates))
            rationale = "改时段等于改动硬性要求 → 必须标待确认"
        else:
            facts.no_alternative = True
            _add_pending(facts, PendingItem("slot_change", "当日窗口内无可用时段，请调整窗口或日期"))
            result = ToolResult(ok=False, code="NO_ALTERNATIVE", err="no free slot in window")
            summary = "当日窗口内无可用替代时段"
            rationale = "无法满足时段要求 → 只能请求确认，不得强行安排"
        return result, args, summary, rationale

    if action is Action.ASK:
        facts.asked = True
        if facts.material is not None and not facts.material_complete:
            missing = facts.material.missing_fields()
            _add_pending(facts, PendingItem("missing_info", "缺少 %s，请补充后再排期" % "、".join(missing)))
            summary = "已生成补查请求：缺少 %s" % "、".join(missing)
            rationale = "信息不足，不能臆测字段 → 请求补查"
        else:
            _add_pending(facts, PendingItem("confirm", "请确认建议时段与风险处理后再保存草稿"))
            summary = "已生成确认请求"
            rationale = "改动需用户同意 → 请求确认而非直接保存"
        return ToolResult(ok=True, code="ASK"), {}, summary, rationale

    if action is Action.BLOCK:
        facts.blocked = True
        summary = "已阻断"
        rationale = "存在 block 风险，不能生成草稿或发布"
        return ToolResult(ok=True, code="BLOCKED"), {}, summary, rationale

    if action is Action.SAVE:
        draft = _build_draft(facts, material_id)
        args = {"material_id": material_id, "suggested_slot": draft.suggested_slot.label() if draft.suggested_slot else None}
        result = toolbox.save_draft(draft)
        if result.ok:
            outcome = result.data
            facts.save_kind = outcome.kind
            facts.draft = outcome.draft
            summary = "保存结果=%s，revision=%d" % (outcome.kind, outcome.draft.revision)
            if outcome.kind == "idempotent":
                rationale = "指纹相同 → 幂等命中，未新增草稿"
            else:
                rationale = "已写入草稿（仅草稿，未发布）"
        else:
            facts.save_kind = "failed"
            facts.save_failed = True
            summary = "保存失败：%s" % result.err
            rationale = "保存失败必须如实返回，不自动重试（重试须是显式决策）"
        return result, args, summary, rationale

    summary = "流程结束"
    rationale = "已得到明确结论"
    return ToolResult(ok=True, code="END"), {}, summary, rationale


def _finalize(facts: Facts) -> RunStatus:
    if facts.tool_error:
        return RunStatus.TOOL_FAILED
    if facts.fetch_failed:
        return RunStatus.NEEDS_INFO
    if facts.save_kind == "failed":
        return RunStatus.FAILED
    if facts.blocked or facts.has_block:
        return RunStatus.BLOCKED
    if facts.draft is not None:
        return RunStatus.NEEDS_CONFIRM if facts.draft.pending else RunStatus.DONE
    if facts.material is None or not facts.material_complete:
        return RunStatus.NEEDS_INFO if facts.asked else RunStatus.INCOMPLETE
    # 素材完整、却既没保存也没请求确认 —— 这是流程被提前掐断，不是"保存失败"
    return RunStatus.NEEDS_CONFIRM if facts.asked else RunStatus.INCOMPLETE


def _message(facts: Facts, status: RunStatus) -> str:
    if status is RunStatus.DONE:
        return "草稿已保存，无待确认项（未发布）"
    if status is RunStatus.NEEDS_CONFIRM:
        if facts.draft is not None:
            return "草稿已保存，含待确认项，等待用户确认（未发布）"
        return "未保存，已请求用户确认（未发布）"
    if status is RunStatus.NEEDS_INFO:
        if facts.fetch_failed:
            return "素材不存在或不可读，未生成草稿"
        return "素材信息不足，已请求补查，未生成草稿"
    if status is RunStatus.BLOCKED:
        return "规则阻断，未生成草稿"
    if status is RunStatus.FAILED:
        return "草稿保存失败，未写入任何数据"
    if status is RunStatus.TOOL_FAILED:
        return "工具执行失败（%s），本轮中止，未生成草稿" % (facts.tool_error_detail or "未知")
    if status is RunStatus.INCOMPLETE:
        if facts.material is None:
            return "流程在读取素材前就被结束，未生成草稿"
        return "流程提前结束，未生成草稿（既未保存也未请求确认）"
    return "达到步数或工具调用上限，未完成"


def _build_item(facts: Facts, material_id: str, status: RunStatus) -> ItemResult:
    material = facts.material
    suggested = facts.candidate or (material.requested_slot if material else None)
    pending = facts.draft.pending if facts.draft is not None else list(facts.pending)
    return ItemResult(
        material_id=material_id,
        audit_status=_audit_status(facts),
        risk_reasons=[f.msg for f in facts.findings],
        suggested_slot=suggested,
        pending_items=list(pending),
        draft_status=_SAVE_KIND_TO_STATUS.get(facts.save_kind, DraftStatus.NOT_SAVED),
        run_status=status,
        message=_message(facts, status),
    )


def run(
    material_id: str,
    toolbox: Toolbox,
    decide: Decider,
    limits: Limits = None,
    trace_path: str = None,
) -> RunResult:
    lim = limits or Limits()
    facts = Facts()
    steps: List[StepRecord] = []
    tool_calls = 0
    terminal = False
    end_reason = ""

    with TraceWriter(trace_path) as writer:
        for step in range(1, lim.max_steps + 1):
            allowed = allowed_actions(facts)
            requested = decide(facts, allowed)
            decision_reason = getattr(decide, "last_reason", "") or ""
            action = requested
            if action not in allowed:
                action = pick(allowed)
                facts.notes.append("动作 %s 不在允许集合内，回退为 %s" % (requested, action))

            if action in TOOL_NAMES and tool_calls >= lim.max_tool_calls:
                action = Action.END
                end_reason = "tool_call_limit"

            result, args, summary, rationale = _execute(action, facts, toolbox, material_id)
            if decision_reason:
                rationale = "%s｜决策依据：%s" % (rationale, decision_reason)
            if action in TOOL_NAMES:
                tool_calls += 1

            record = StepRecord(step, action.value, TOOL_NAMES.get(action), args, summary, rationale)
            writer.write(record)
            steps.append(record)

            if action is Action.END or action is Action.BLOCK:
                terminal = True
                end_reason = end_reason or action.value
                break

    if not terminal:
        end_reason = "step_limit"

    status = _finalize(facts) if terminal else RunStatus.LIMIT_REACHED
    item = _build_item(facts, material_id, status)
    return RunResult(status=status, item=item, steps=steps, trace_path=str(trace_path) if trace_path else "")
