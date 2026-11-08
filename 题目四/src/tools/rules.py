"""品牌规则检查工具。

职责边界（冲突 2 的裁定）：**不检查时长**。时长由 estimate 独占，
避免 DURATION_EXCEEDED 被两处重复产出、修正计数翻倍。
本工具只做文本级、可枚举、可复算的检查。

事实溯源的三层校验（对应“编造产品事实属关键缺陷”）：
  UNSOURCED_CLAIM   分镜没有绑定任何素材 ID
  NO_FACT_REF       分镜没有引用任何一条素材原文事实
  INVALID_FACT_REF  引用了不存在的素材，或引用了该素材并不存在的事实（编造）
  FACT_REF_NOT_IN_SHOT  事实引用的素材未出现在该分镜的 material_ids 中
"""

from __future__ import annotations

from typing import Dict, List

from ..config import Ctx
from ..domain.errors import ErrorCode
from ..domain.schemas import ScriptDraft, ToolResult


def _violation(code: str, msg: str, shot_id: str = "", **extra) -> dict:
    item = {"code": code, "msg": msg}
    if shot_id:
        item["shot_id"] = shot_id
    item.update(extra)
    return item


def check(draft: ScriptDraft, rules: Dict, ctx: Ctx) -> ToolResult:
    if draft is None:
        return ToolResult(
            tool="check_rules",
            ok=False,
            digest="无可检查的脚本",
            error_code=ErrorCode.TOOL_ERROR.value,
            message="draft is None",
        )

    violations: List[dict] = []
    full_text = " ".join(
        [draft.title]
        + [s.narration for s in draft.shots]
        + [s.subtitle for s in draft.shots]
    )

    for phrase in rules.get("forbidden_phrases", []) or []:
        if phrase and phrase in full_text:
            violations.append(_violation("FORBIDDEN_PHRASE", "出现禁用表述：{p}".format(p=phrase)))

    for phrase in rules.get("required_mentions", []) or []:
        if phrase and phrase not in full_text:
            violations.append(_violation("MISSING_REQUIRED", "缺少必提信息：{p}".format(p=phrase)))

    for shot in draft.shots:
        sid = shot.shot_id

        if not shot.material_ids:
            violations.append(_violation("UNSOURCED_CLAIM", "分镜未绑定素材 ID", sid))

        if not shot.fact_refs:
            violations.append(
                _violation("NO_FACT_REF", "分镜未引用任何素材原文事实，无法溯源", sid)
            )
            continue

        for ref in shot.fact_refs:
            mid = str(ref.get("material_id") or "")
            fact = str(ref.get("fact") or "")
            item = ctx.materials.get(mid)
            if item is None:
                violations.append(
                    _violation(
                        "INVALID_FACT_REF",
                        "引用了不存在的素材：{m}".format(m=mid),
                        sid,
                        material_id=mid,
                    )
                )
                continue
            available = "、".join(item.get("facts") or []) or "无"
            if fact not in (item.get("facts") or []):
                # 回灌给模型的必须是可行动的观察：错在哪、正确可用的是什么
                violations.append(
                    _violation(
                        "INVALID_FACT_REF",
                        "素材 {m} 中没有「{f}」，该素材可用事实：{a}".format(
                            m=mid, f=fact, a=available
                        ),
                        sid,
                        material_id=mid,
                    )
                )
                continue
            if mid not in shot.material_ids:
                violations.append(
                    _violation(
                        "FACT_REF_NOT_IN_SHOT",
                        "事实引用的素材 {m} 未出现在该分镜的 material_ids 中".format(m=mid),
                        sid,
                        material_id=mid,
                    )
                )

    passed = not violations
    if passed:
        digest = "规则检查通过（0 项违规，事实引用全部可溯源）"
    else:
        brief = []
        for v in violations[:3]:
            prefix = v.get("shot_id") or "-"
            brief.append("[{s}] {m}".format(s=prefix, m=v["msg"]))
        digest = "规则检查未通过（{n} 项）：{p}".format(
            n=len(violations), p="；".join(brief)
        )
    return ToolResult(
        tool="check_rules",
        ok=True,
        digest=digest,
        data={"passed": passed, "violations": violations, "count": len(violations)},
        error_code=None if passed else ErrorCode.RULE_VIOLATION.value,
    )
