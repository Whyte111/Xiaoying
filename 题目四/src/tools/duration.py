"""时长估算工具。

职责边界（B2）：它是分镜时长的**唯一来源**。模型给的秒数一律忽略。
确定性：同样输入必然得到同样输出，可被评审复算。
"""

from __future__ import annotations

from typing import List

from ..domain.errors import ErrorCode
from ..domain.schemas import ScriptDraft, ToolResult


def count_speech_units(text: str) -> int:
    """中文按字计数：非空白字符数。"""
    return sum(1 for ch in text if not ch.isspace())


def estimate(draft: ScriptDraft, words_per_second: float, target_duration_s: float) -> ToolResult:
    if draft is None or not draft.shots:
        return ToolResult(
            tool="estimate",
            ok=False,
            digest="无分镜可估算",
            error_code=ErrorCode.TOOL_ERROR.value,
            message="draft 不存在或 shots 为空",
        )
    if words_per_second <= 0:
        return ToolResult(
            tool="estimate",
            ok=False,
            digest="语速配置非法",
            error_code=ErrorCode.TOOL_ERROR.value,
            message="words_per_second <= 0",
        )

    per_shot: List[dict] = []
    total = 0.0
    for shot in draft.shots:
        units = count_speech_units(shot.narration)
        # 单镜最短 1.0 秒，保留 1 位小数以保证可复算
        seconds = max(1.0, round(units / words_per_second, 1))
        per_shot.append(
            {"shot_id": shot.shot_id, "speech_units": units, "duration_s": seconds}
        )
        total += seconds

    total = round(total, 1)
    passed = total <= float(target_duration_s) + 1e-9

    total_units = sum(item["speech_units"] for item in per_shot)
    budget_units = int(float(target_duration_s) * words_per_second)
    need_cut = max(0, total_units - budget_units)

    if passed:
        digest = "总时长 {total}s / 目标 {target}s，通过（旁白 {u} 字）".format(
            total=total, target=target_duration_s, u=total_units
        )
    else:
        # 观察必须可行动：告诉模型还差多少字，否则它只能象征性地删两个字
        digest = (
            "总时长 {total}s / 目标 {target}s，超出；当前旁白 {u} 字，"
            "按 {w} 字/秒目标上限约 {b} 字，需至少减少 {d} 字或减少分镜数"
        ).format(
            total=total,
            target=target_duration_s,
            u=total_units,
            w=words_per_second,
            b=budget_units,
            d=need_cut,
        )
    return ToolResult(
        tool="estimate",
        ok=True,
        args={"words_per_second": words_per_second, "target_duration_s": target_duration_s},
        digest=digest,
        data={
            "total_s": total,
            "target_s": float(target_duration_s),
            "passed": passed,
            "per_shot": per_shot,
            "total_units": total_units,
            "budget_units": budget_units,
            "need_cut": need_cut,
        },
        error_code=None if passed else ErrorCode.DURATION_EXCEEDED.value,
    )
