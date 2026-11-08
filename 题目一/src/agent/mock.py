"""离线 Mock 决策源：按观察到的工具结果做规则判断，模拟模型的选择行为。

边界：它仍是人写的规则，只证明流程可跑，不证明真实模型的决策质量。
"""
from __future__ import annotations

from typing import Set

from ..domain.models import Action, Facts, Severity


class RuleBasedMock:
    def __init__(self) -> None:
        self.last_reason = ""

    def __call__(self, facts: Facts, allowed: Set[Action]) -> Action:
        return self.decide(facts, allowed)

    def decide(self, facts: Facts, allowed: Set[Action]) -> Action:
        # 有冲突：先换时段，但改硬性要求必须先确认
        if Action.RESCHEDULE in allowed:
            self.last_reason = "请求时段被占用，先找替代时段再请用户确认"
            return Action.RESCHEDULE

        # 有 warn 风险：不擅自保存，先请用户确认
        if any(f.severity is Severity.WARN for f in facts.findings) and Action.ASK in allowed:
            self.last_reason = "存在 warn 风险，需用户确认后再保存草稿"
            return Action.ASK

        # 已请求确认：本轮停下等用户，不擅自保存
        if facts.asked and Action.END in allowed:
            self.last_reason = "已请求用户确认，本轮不保存"
            return Action.END

        if Action.SAVE in allowed:
            self.last_reason = "无阻断风险，保存草稿并保留待确认项"
            return Action.SAVE

        if Action.BLOCK in allowed:
            self.last_reason = "存在 block 风险，直接阻断"
            return Action.BLOCK

        if Action.ASK in allowed:
            self.last_reason = "信息不足，请求补查"
            return Action.ASK

        for candidate in (Action.FETCH, Action.CHECK, Action.QUERY, Action.END):
            if candidate in allowed:
                self.last_reason = "按流程推进到 %s" % candidate.value
                return candidate
        return Action.END
