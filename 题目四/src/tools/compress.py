"""确定性压缩工具。

设计要点：
- 模型只负责**决定何时压缩**，不负责算术；
- 同一份草案 + 同一目标时长，必然得到同一份结果（可复现、可复算）；
- 压缩只动旁白，**不动** material_ids / fact_refs，因此事实溯源关系不会被破坏。

算法：
1. 每镜至少 1 秒，若 目标时长 < 分镜数，直接判定不可满足；
2. 从"当前时长最长"的分镜开始逐字削减，直到总时长落入目标；
3. 最后按标点回退收尾，避免出现半句话。
"""

from __future__ import annotations

from typing import Dict, List

from ..domain.errors import ErrorCode
from ..domain.schemas import ScriptDraft, ToolResult
from .duration import count_speech_units

_PUNCT = "。！？；，、,.!?;"
MIN_SECONDS_PER_SHOT = 1.0
_MAX_ITERATIONS = 8000
# 内容底线：为了凑时长而删掉超过一半旁白，属于把内容压没，应判不可满足而非硬凑
MIN_KEEP_RATIO = 0.5


def _duration(units: int, wps: float) -> float:
    return max(MIN_SECONDS_PER_SHOT, round(units / wps, 1))


def _trim_to_units(text: str, limit: int) -> str:
    if count_speech_units(text) <= limit:
        return text
    out: List[str] = []
    used = 0
    for ch in text:
        if not ch.isspace():
            if used >= limit:
                break
            used += 1
        out.append(ch)
    trimmed = "".join(out).rstrip()
    # 只在末尾 30% 范围内回退到标点，避免为了一个句号把大段内容砍掉
    floor = int(len(trimmed) * 0.7)
    for i in range(len(trimmed) - 1, floor - 1, -1):
        if trimmed[i] in _PUNCT:
            return trimmed[: i + 1]
    return trimmed


def compress(
    draft: ScriptDraft, target_duration_s: float, words_per_second: float
) -> ToolResult:
    if draft is None or not draft.shots:
        return ToolResult(
            tool="compress",
            ok=False,
            digest="没有可压缩的草案",
            error_code=ErrorCode.TOOL_ERROR.value,
            message="draft is None or empty",
        )
    if words_per_second <= 0:
        return ToolResult(
            tool="compress",
            ok=False,
            digest="语速配置非法",
            error_code=ErrorCode.TOOL_ERROR.value,
            message="words_per_second <= 0",
        )

    shots = draft.shots
    n = len(shots)
    target = float(target_duration_s)

    if n * MIN_SECONDS_PER_SHOT > target + 1e-9:
        return ToolResult(
            tool="compress",
            ok=False,
            digest=(
                "目标 {t}s 无法容纳 {n} 个分镜（每镜至少 1 秒），"
                "请减少分镜数或提高目标时长"
            ).format(t=target, n=n),
            error_code=ErrorCode.UNSATISFIABLE.value,
            data={"shots": n, "target_s": target},
        )

    texts = [s.narration for s in shots]
    units = [max(1, count_speech_units(t)) for t in texts]
    before_s = round(sum(_duration(u, words_per_second) for u in units), 1)

    def total() -> float:
        return round(sum(_duration(u, words_per_second) for u in units), 1)

    guard = 0
    while total() > target + 1e-9 and guard < _MAX_ITERATIONS:
        # 每次从当前时长最长的分镜削一个字；平手时取靠前的，保证确定性
        idx = max(range(n), key=lambda i: (_duration(units[i], words_per_second), -i))
        if units[idx] <= 1:
            break
        units[idx] -= 1
        guard += 1

    after_s = total()
    if after_s > target + 1e-9:
        return ToolResult(
            tool="compress",
            ok=False,
            digest="压缩到每镜 1 字仍超出目标 {t}s".format(t=target),
            error_code=ErrorCode.UNSATISFIABLE.value,
            data={"before_s": before_s, "after_s": after_s, "target_s": target},
        )

    per_shot: List[Dict] = []
    for shot, limit, original in zip(shots, units, texts):
        new_text = _trim_to_units(original, limit)
        per_shot.append(
            {
                "shot_id": shot.shot_id,
                "narration": new_text,
                "units": count_speech_units(new_text),
            }
        )

    # 报给模型的必须是**裁剪后实际**的时长，不能是计划值：
    # 标点回退会多切一点，若报计划值，下一次 estimate 会对不上。
    final_units = [p["units"] for p in per_shot]
    after_s = round(sum(_duration(u, words_per_second) for u in final_units), 1)

    before_units = sum(max(1, count_speech_units(t)) for t in texts)
    kept = sum(final_units)
    if before_units > 0 and kept < MIN_KEEP_RATIO * before_units:
        return ToolResult(
            tool="compress",
            ok=False,
            digest=(
                "目标 {t}s 需要删掉超过 {p}% 的旁白（{b} → {a} 字），"
                "压缩会损失主要内容，判定为不可满足；请提高目标时长或减少信息量"
            ).format(
                t=target,
                p=int((1 - MIN_KEEP_RATIO) * 100),
                b=before_units,
                a=kept,
            ),
            error_code=ErrorCode.UNSATISFIABLE.value,
            data={
                "before_s": before_s,
                "after_s": after_s,
                "before_units": before_units,
                "kept_units": kept,
                "target_s": target,
            },
        )

    return ToolResult(
        tool="compress",
        ok=True,
        args={"target_duration_s": target, "words_per_second": words_per_second},
        digest="已确定性压缩：{a}s → {b}s / 目标 {t}s（旁白 {u} 字）".format(
            a=before_s, b=after_s, t=target, u=sum(final_units)
        ),
        data={
            "before_s": before_s,
            "after_s": after_s,
            "target_s": target,
            "per_shot": per_shot,
        },
    )
