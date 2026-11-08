"""决策器：唯一接口 DecisionPolicy.next(observation) -> Action。

MockPolicy 只读 observation，不看轮次；失败后走降级路径，而不是原样重试。
"""
from __future__ import annotations

from typing import Any, Dict, List

from .guards import BUDGET_EXCEEDED
from .types import (
    ACTION_CALL_TOOL,
    ACTION_FINISH,
    ACTION_STOP,
    Action,
    Observation,
)


class DecisionPolicy:
    def next(self, obs: Observation) -> Action:  # pragma: no cover - 接口占位
        raise NotImplementedError


class MockPolicy(DecisionPolicy):
    """离线确定性决策器：观察 -> 降级/换方案/停止。"""

    MAX_ATTEMPTS = 2

    def __init__(self, scenario: Dict[str, Any]) -> None:
        self.scenario = scenario
        self.options: List[str] = [o["id"] for o in scenario["plan_options"]]
        self.save_fee = float(scenario["fee_table"]["save_plan"]["cost"])

    def next(self, obs: Observation) -> Action:
        # 0. 副作用未知（如保存超时）优先处理：停止，避免重复保存
        if obs.last_result is not None and obs.last_result.error_code == "OUTCOME_UNKNOWN":
            return Action(
                ACTION_STOP,
                reason="保存操作超时，落盘结果未知，停止以避免重复保存，请人工确认产物",
            )

        # 1. 素材
        if not obs.materials:
            n = obs.attempts.get("search_material", 0)
            if n >= self.MAX_ATTEMPTS:
                if obs.last_result is not None and obs.last_result.ok:
                    return Action(
                        ACTION_STOP,
                        reason="素材库中没有匹配内容，检索本身成功但无可用素材",
                    )
                return Action(ACTION_STOP, reason=f"素材检索连续失败 {n} 次，无可用素材")
            if n == 0:
                return Action(ACTION_CALL_TOOL, "search_material", {"query": obs.topic}, "尚无素材，先检索")
            return Action(
                ACTION_CALL_TOOL, "search_material", {"query": ""},
                "上次检索失败，放宽为全量检索再试",
            )

        # 2. 脚本
        if obs.script is None:
            n = obs.attempts.get("generate_script", 0)
            if n >= self.MAX_ATTEMPTS:
                return Action(ACTION_STOP, reason=f"脚本生成连续失败 {n} 次，已保留素材结果")
            if n == 0:
                ids = [m["id"] for m in obs.materials]
                return Action(
                    ACTION_CALL_TOOL, "generate_script", {"material_ids": ids},
                    f"已获得 {len(ids)} 条素材，生成脚本",
                )
            # 降级：只用第一条素材重新生成
            return Action(
                ACTION_CALL_TOOL, "generate_script", {"material_ids": [obs.materials[0]["id"]]},
                f"上次脚本生成失败，降级为单素材（{obs.materials[0]['id']}）重试",
            )

        # 3. 成本估算
        if obs.estimate is None:
            n = obs.attempts.get("estimate_cost", 0)
            if n >= self.MAX_ATTEMPTS:
                return Action(ACTION_STOP, reason=f"成本估算连续失败 {n} 次，无法核预算")
            untried = [o for o in self.options if o not in obs.tried_options]
            if not untried:
                return Action(ACTION_STOP, reason="所有备选方案都已尝试，无可用方案")
            return Action(
                ACTION_CALL_TOOL, "estimate_cost", {"option_id": untried[0]},
                f"估算方案 {untried[0]} 的成本",
            )

        # 4. 预算约束（由 guards 计算，决策器只负责选路）
        budget_v = next((v for v in obs.violations if v.code == BUDGET_EXCEEDED), None)
        if budget_v is not None:
            untried = [o for o in self.options if o not in obs.tried_options]
            if untried:
                return Action(
                    ACTION_CALL_TOOL, "estimate_cost", {"option_id": untried[0]},
                    f"{budget_v.detail}；改选方案 {untried[0]} 重新估算",
                )
            return Action(ACTION_STOP, reason=budget_v.detail)

        # 5. 保存
        if obs.saved is None:
            n = obs.attempts.get("save_plan", 0)
            if n >= self.MAX_ATTEMPTS:
                return Action(
                    ACTION_STOP,
                    reason=f"方案保存连续失败 {n} 次，内容已生成但未落盘",
                )
            need = obs.spent + float(obs.estimate["total_cost"]) + self.save_fee
            return Action(
                ACTION_CALL_TOOL, "save_plan",
                {
                    "topic": obs.topic,
                    "option_id": obs.estimate["option_id"],
                    "total_cost": round(need, 2),
                    "script": obs.script,
                },
                f"方案 {obs.estimate['option_id']} 预计总花费 {need:.2f} 在预算内，保存",
            )

        return Action(ACTION_FINISH, reason="方案已保存，任务完成")
