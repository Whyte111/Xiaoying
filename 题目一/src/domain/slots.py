"""时段冲突与替代候选：纯函数，单时区、分钟精度、不跨天。"""
from __future__ import annotations

from datetime import timedelta
from typing import List

from .models import Conflict, ExistingSlot, Slot


def overlaps(a: Slot, b: Slot) -> bool:
    """半开区间：首尾相接不算冲突。时区口径不一致时明确报错，而不是静默算错。"""
    if (a.start.tzinfo is None) != (b.start.tzinfo is None):
        raise ValueError("两个时段的时区口径不一致，无法比较：%s 与 %s" % (a.label(), b.label()))
    return a.start < b.end and b.start < a.end


def find_conflicts(slot: Slot, existing: List[ExistingSlot]) -> List[Conflict]:
    return [Conflict(slot=slot, against_id=e.id, kind="overlap") for e in existing if overlaps(slot, e.slot)]


def suggest_alternatives(
    requested: Slot,
    existing: List[ExistingSlot],
    limit: int = 3,
    step_minutes: int = 60,
) -> List[Slot]:
    """从请求时段往后按固定步长找可用时段，只取同一天，最多 limit 个。"""
    out: List[Slot] = []
    cursor = requested
    span = timedelta(minutes=step_minutes)
    guard = 0
    while len(out) < limit and guard < 48:
        guard += 1
        cursor = Slot(cursor.start + span, cursor.end + span)
        if cursor.start.date() != requested.start.date():
            break
        if not find_conflicts(cursor, existing):
            out.append(cursor)
    return out
