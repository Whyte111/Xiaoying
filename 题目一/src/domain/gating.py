"""动作门控：由事实集合推导当前允许的动作，非法动作在此被挡掉。"""
from __future__ import annotations

from typing import Set

from .models import Action, Facts


def allowed_actions(facts: Facts) -> Set[Action]:
    if facts.fetch_failed or facts.save_failed or facts.tool_error:
        return {Action.END}

    if facts.material is None:
        return {Action.FETCH, Action.END}

    if not facts.material_complete:
        return {Action.END} if facts.asked else {Action.ASK, Action.END}

    if not facts.rules_checked:
        return {Action.CHECK, Action.END}

    if facts.has_block:
        return {Action.BLOCK, Action.END}

    if not facts.conflicts_checked:
        return {Action.QUERY, Action.END}

    if facts.conflicts and facts.candidate is None:
        if facts.no_alternative:
            return {Action.END} if facts.asked else {Action.ASK, Action.END}
        # 已经请求过一次确认就不要再问，否则会空转到上限
        if facts.asked:
            return {Action.RESCHEDULE, Action.END}
        return {Action.RESCHEDULE, Action.ASK, Action.END}

    if facts.draft is not None:
        return {Action.END}

    if facts.asked:
        return {Action.SAVE, Action.END}

    return {Action.SAVE, Action.ASK, Action.END}
