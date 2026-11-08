"""确定性策略：无模型时的决策源（也是离线 Mock 入口）。

它只做一件事：在 allowed_actions 给出的集合里按优先级挑一个。
换成模型时，只需替换这个函数，循环与门控不变。
"""
from __future__ import annotations

from typing import Set

from ..domain.models import Action, Facts

#: 同等可行动时的偏好顺序；SAVE 先于 ASK，保证默认会自动落草稿并标待确认
PRIORITY = [
    Action.FETCH,
    Action.CHECK,
    Action.QUERY,
    Action.RESCHEDULE,
    Action.SAVE,
    Action.ASK,
    Action.BLOCK,
    Action.END,
]


def pick(allowed: Set[Action]) -> Action:
    for action in PRIORITY:
        if action in allowed:
            return action
    return Action.END


def decide(facts: Facts, allowed: Set[Action]) -> Action:
    return pick(allowed)
