"""约束检查：只判断、不修复、不改状态。

返回 Violation 列表交给决策器，由决策器决定换方案还是停止。
"""
from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from .types import PlanItem, STEP_SUCCEEDED, TaskInput, Violation

INPUT_INVALID = "INPUT_INVALID"
BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
TIME_EXCEEDED = "TIME_EXCEEDED"
DEP_UNMET = "DEP_UNMET"

MAX_TOPIC_LEN = 2000


def safe_topic(topic: Any, limit: int = MAX_TOPIC_LEN) -> str:
    """落盘/展示用的主题：超长一律截断，避免坏输入撑大结果文件。"""
    text = "" if topic is None else str(topic)
    if len(text) <= limit:
        return text
    return f"{text[:limit]}…（已截断，原长 {len(text)}）"


def parse_deadline(raw: Any) -> Optional[datetime]:
    """接受 2026-11-20 或 2026-11-20T18:00[:00]；解析失败返回 None。"""
    s = str(raw or "").strip().replace("/", "-")
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        pass
    try:
        return datetime.combine(date.fromisoformat(s), datetime.min.time())
    except ValueError:
        return None


def check_input(task: TaskInput) -> List[Violation]:
    out: List[Violation] = []
    topic = task.topic or ""
    if not topic.strip():
        out.append(Violation(INPUT_INVALID, "缺少主题，无法开始规划"))
    elif len(topic) > MAX_TOPIC_LEN:
        out.append(
            Violation(INPUT_INVALID, f"主题过长（{len(topic)} 字符，上限 {MAX_TOPIC_LEN}）")
        )
    if task.budget is None:
        out.append(Violation(INPUT_INVALID, "缺少预算，无法判断方案是否可行"))
    elif (
        isinstance(task.budget, bool)
        or not isinstance(task.budget, (int, float))
        or not math.isfinite(float(task.budget))
        or float(task.budget) <= 0
    ):
        out.append(
            Violation(INPUT_INVALID, f"预算必须是有限正数，收到 {task.budget!r}")
        )
    if not str(task.deadline or "").strip():
        out.append(Violation(INPUT_INVALID, "缺少截止时间，无法确认交付窗口"))
    elif parse_deadline(task.deadline) is None:
        out.append(Violation(
            INPUT_INVALID,
            f"截止时间格式无法解析: {task.deadline}（应形如 2026-11-20 或 2026-11-20T18:00）",
        ))
    return out


def check_budget(
    spent: float,
    estimate: Optional[Dict[str, Any]],
    budget: Optional[float],
    save_fee: float,
    saved: bool = False,
) -> List[Violation]:
    if estimate is None or budget is None:
        return []
    # 已保存时 spent 里已经包含保存费，不能再加一次
    fee = 0.0 if saved else save_fee
    need = spent + float(estimate["total_cost"]) + fee
    if need <= budget:
        return []
    gap = need - budget
    breakdown = f"（方案 {estimate['total_cost']:.2f} + 已付工具费 {spent:.2f}"
    if fee:
        breakdown += f" + 保存费 {fee:.2f}"
    breakdown += "）"
    return [
        Violation(
            BUDGET_EXCEEDED,
            f"方案 {estimate['option_id']} 预计总花费 {need:.2f}{breakdown}"
            f"超出预算 {budget:.2f}，缺口 {gap:.2f}",
        )
    ]


def check_deps(plan: List[PlanItem]) -> List[Violation]:
    """依赖未满足却已执行的步骤，属于状态不一致。"""
    done = {p.step_id for p in plan if p.status == STEP_SUCCEEDED}
    out: List[Violation] = []
    for item in plan:
        if item.status != STEP_SUCCEEDED:
            continue
        missing = [d for d in item.deps if d not in done]
        if missing:
            out.append(Violation(DEP_UNMET, f"步骤 {item.step_id} 已成功，但依赖 {missing} 未成功"))
    return out


def check_time(
    time_budget: Optional[float], estimate: Optional[Dict[str, Any]]
) -> List[Violation]:
    """时间可行性判断。

    需要“可用工时”这一输入，而题目方只提供截止日期，未提供可用工时，
    因此默认传 None -> 恒不触发。将来若引入 time_budget，本函数即可生效。
    """
    if time_budget is None or estimate is None:
        return []
    est = float(estimate.get("est_duration", 0.0))
    if est <= float(time_budget):
        return []
    return [
        Violation(
            TIME_EXCEEDED,
            f"方案 {estimate['option_id']} 预计耗时 {est:.2f} 超过可用时间 {float(time_budget):.2f}",
        )
    ]
