"""离线 Mock 模型：**状态驱动的确定性假模型**。

用途与边界（务必如实告知评审）：
- 它只用于验证流程与循环接线，**不证明真实模型的决策效果**；
- 它的分支是硬编码的 if/else，因此它的 trace 不能用来证明 ReAct 能力；
- 它与真实模型实现同一 `decide()` 接口，走同一条循环，不另开路径。
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from ..domain.schemas import ActionDecision, LoopState


class MockModel:
    name = "mock"

    def __init__(self, compress_factor: float = 0.73) -> None:
        self.compress_factor = compress_factor

    # ---- ModelClient 接口 ----
    def decide(self, state: LoopState) -> ActionDecision:
        task = state.task

        if state.draft is None:
            if not state.searched:
                return ActionDecision(
                    "search",
                    {"query": task.topic, "ids": task.material_ids},
                    "尚未检索素材，先按主题检索，避免凭空编造产品事实",
                )
            if not state.search_hit:
                tried_rewrite = any(o.action == "rewrite_query" for o in state.observations)
                if not tried_rewrite:
                    return ActionDecision(
                        "rewrite_query",
                        {"query": task.topic},
                        "首次检索无结果，先换关键词重试，而不是直接请求补充",
                    )
                return ActionDecision(
                    "ask_clarify",
                    {"question": "没有检索到可用素材，请补充产品资料或指定素材 ID。"},
                    "多次检索无结果，且不允许编造事实，因此请求补充",
                )
            return ActionDecision(
                "compose", self._first_draft(state), "已有素材，生成首稿"
            )

        if state.last_estimate is None:
            basis = (
                "首稿完成，先估算总时长再谈交付"
                if state.revision <= 1
                else "改稿完成，重新估算总时长"
            )
            return ActionDecision("estimate", {}, basis)

        if not state.last_estimate.get("passed"):
            return ActionDecision(
                "compress",
                {},
                "总时长 {a}s 超出目标 {b}s，调用确定性压缩".format(
                    a=state.last_estimate.get("total_s"),
                    b=state.last_estimate.get("target_s"),
                ),
            )

        if state.last_rules is None:
            return ActionDecision("check_rules", {}, "时长已通过，检查品牌规则")

        if not state.last_rules.get("passed"):
            return ActionDecision(
                "compose", self._fix_rules(state), "规则未通过，补齐素材与事实引用后重新检查"
            )

        return ActionDecision("finish", {}, "时长与规则均通过，结束并交付")

    # ---- 内部：确定性内容构造 ----
    def _anchors(self, state: LoopState) -> List[Tuple[str, str]]:
        """每个命中素材取第一条 fact 作为可引用锚点。"""
        out: List[Tuple[str, str]] = []
        for hit in state.search_hits or [{"id": "P01", "facts": ["支持字幕"]}]:
            facts = hit.get("facts") or []
            out.append((hit["id"], facts[0] if facts else "支持字幕"))
        return out or [("P01", "支持字幕")]

    def _first_draft(self, state: LoopState) -> Dict[str, Any]:
        anchors = self._anchors(state)

        def at(i: int) -> Tuple[str, str]:
            return anchors[i if i < len(anchors) else 0]

        # Mock 按 4.5 字/秒估算，并刻意写到目标的约 1.15 倍：
        # 这样离线跑也能演示"超时长 → compress → 通过"的修正闭环。
        target = float(state.task.target_duration_s or 30)
        budget = max(12, int(target * 4.5 * 1.25))
        shot_count = 4 if budget >= 48 else (3 if budget >= 30 else 2)
        per_shot = max(3, budget // shot_count)

        base = [
            (
                "手持手机拍摄的创作者，画面由暗转亮",
                "开场：现在做短视频的人越来越多，真正卡住大家的往往不是创意，而是拍完之后整理素材再到剪出成片这一整段流程，重复又耗时。",
                "创意不缺，流程太累",
            ),
            (
                "产品界面特写，字幕图层出现",
                "中段：这款智能剪辑器{fact}，把素材整理好以后就可以直接进入成片环节，把重复的机械操作交给工具处理。",
                "把重复操作交给工具",
            ),
            (
                "屏幕录制，导出设置面板",
                "细节：出片之前可以再确认一遍画面与文字信息，避免返工，让一个人做号也能把流程跑顺。",
                "出片前再确认一遍",
            ),
            (
                "创作者点击发布，微笑",
                "收尾：如果你也在为成片流程发愁，可以先从基础功能试起，再决定要不要把整套工作流搬过来。",
                "先从基础功能试起",
            ),
        ]

        shots = []
        for i in range(shot_count):
            mid, fact = at(i)
            visual, template, subtitle = base[i]
            shots.append(
                {
                    "shot_id": "s{n}".format(n=i + 1),
                    "visual": visual,
                    "narration": template.format(fact=fact)[:per_shot],
                    "subtitle": subtitle,
                    "material_ids": [mid],
                    "fact_refs": [{"material_id": mid, "fact": fact}],
                }
            )

        return {
            "title": "把重复剪辑交给工具：一个创作者的成片流程",
            "shots": shots,
            "risks": ["当前素材仅包含产品功能描述，不含任何效果或性能数据"],
        }

    def _compress(self, state: LoopState) -> Dict[str, Any]:
        draft = state.draft
        shots = []
        for s in draft.shots:
            keep = max(4, int(len(s.narration) * self.compress_factor))
            shots.append(
                {
                    "shot_id": s.shot_id,
                    "visual": s.visual,
                    "narration": s.narration[:keep],
                    "subtitle": s.subtitle,
                    "material_ids": list(s.material_ids),
                    "fact_refs": list(s.fact_refs),
                }
            )
        return {"title": draft.title, "shots": shots, "risks": list(draft.risks)}

    def _fix_rules(self, state: LoopState) -> Dict[str, Any]:
        draft = state.draft
        mid, fact = self._anchors(state)[0]
        shots = [
            {
                "shot_id": s.shot_id,
                "visual": s.visual,
                "narration": s.narration,
                "subtitle": s.subtitle,
                "material_ids": list(s.material_ids) or [mid],
                "fact_refs": list(s.fact_refs) or [{"material_id": mid, "fact": fact}],
            }
            for s in draft.shots
        ]
        return {"title": draft.title, "shots": shots, "risks": list(draft.risks)}
