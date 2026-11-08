"""动作分发器：动作名 → 工具执行。

边界：只做映射与结果落账，不做语义合法性校验（那是 policy 的事）。
"""

from __future__ import annotations

from typing import Any, Dict

from ..config import Ctx
from ..domain.errors import ErrorCode
from ..domain.schemas import LoopState, ScriptDraft, Shot, ToolResult, ActionDecision
from ..tools import compress as compress_tool
from ..tools import duration, rules, search

# 结构性上限：防止模型一次提交超大规模草案把系统打死
MAX_SHOTS = 20
MAX_NARRATION_CHARS = 4000


class Dispatcher:
    def __init__(self, ctx: Ctx) -> None:
        self.ctx = ctx

    def dispatch(self, decision: ActionDecision, state: LoopState) -> ToolResult:
        action = decision.action
        args: Dict[str, Any] = decision.args or {}

        if action == "search":
            return self._search(args, state, "search")
        if action == "rewrite_query":
            return self._search(args, state, "rewrite_query")
        if action == "compose":
            return self._compose(args, state)
        if action == "estimate":
            return self._estimate(state)
        if action == "compress":
            return self._compress(state)
        if action == "check_rules":
            return self._check_rules(state)
        return ToolResult(
            tool=action,
            ok=False,
            digest="未知动作：{a}".format(a=action),
            error_code=ErrorCode.TOOL_ERROR.value,
        )

    # ---- 各动作 ----
    def _search(self, args: Dict[str, Any], state: LoopState, tool_name: str) -> ToolResult:
        r = search.search(
            str(args.get("query") or ""), self.ctx, ids=list(args.get("ids") or []) or None
        )
        state.searched = True
        if r.ok:
            state.search_hit = True
            state.search_hits = list(r.data.get("hits", []))
        elif r.error_code == ErrorCode.SEARCH_EMPTY.value:
            state.search_hit = False
        r.tool = tool_name
        return r

    def _compose(self, args: Dict[str, Any], state: LoopState) -> ToolResult:
        raw_shots = args.get("shots")
        if not isinstance(raw_shots, list) or not raw_shots:
            return ToolResult(
                tool="compose",
                ok=False,
                digest="compose 缺少非空的 shots",
                error_code=ErrorCode.TOOL_ERROR.value,
            )

        if len(raw_shots) > MAX_SHOTS:
            return ToolResult(
                tool="compose",
                ok=False,
                digest="分镜数 {n} 超过上限 {m}".format(n=len(raw_shots), m=MAX_SHOTS),
                error_code=ErrorCode.TOOL_ERROR.value,
            )

        shots = []
        unknown = []
        seen_ids = set()
        total_chars = 0
        for i, raw in enumerate(raw_shots):
            if not isinstance(raw, dict):
                return ToolResult(
                    tool="compose",
                    ok=False,
                    digest="shots[{i}] 不是对象".format(i=i),
                    error_code=ErrorCode.TOOL_ERROR.value,
                )

            sid = str(raw.get("shot_id") or "s{n}".format(n=i + 1))
            if sid in seen_ids:
                # 重复 shot_id 会让时长无法与分镜一一对应，评审复算时对不上
                return ToolResult(
                    tool="compose",
                    ok=False,
                    digest="分镜 shot_id 重复：{s}，无法与时长一一对应".format(s=sid),
                    error_code=ErrorCode.TOOL_ERROR.value,
                )
            seen_ids.add(sid)

            narration = str(raw.get("narration") or "")
            if not narration.strip():
                return ToolResult(
                    tool="compose",
                    ok=False,
                    digest="分镜 {s} 的旁白为空".format(s=sid),
                    error_code=ErrorCode.TOOL_ERROR.value,
                )
            total_chars += len(narration)
            if total_chars > MAX_NARRATION_CHARS:
                return ToolResult(
                    tool="compose",
                    ok=False,
                    digest="旁白总字数超过上限 {m}".format(m=MAX_NARRATION_CHARS),
                    error_code=ErrorCode.TOOL_ERROR.value,
                )

            mids = [str(x) for x in (raw.get("material_ids") or [])]
            for m in mids:
                if m not in self.ctx.materials and m not in unknown:
                    unknown.append(m)
            refs = []
            for ref in raw.get("fact_refs") or []:
                if isinstance(ref, dict):
                    refs.append(
                        {
                            "material_id": str(ref.get("material_id") or ""),
                            "fact": str(ref.get("fact") or ""),
                        }
                    )
            shots.append(
                Shot(
                    shot_id=sid,
                    narration=narration,
                    visual=str(raw.get("visual") or ""),
                    subtitle=str(raw.get("subtitle") or ""),
                    material_ids=mids,
                    fact_refs=refs,
                    # 刻意忽略模型自报的 duration_s：时长只由 duration 工具给（B2）
                    duration_s=None,
                )
            )

        if unknown:
            return ToolResult(
                tool="compose",
                ok=False,
                digest="引用了不存在的素材 ID：{ids}".format(ids=",".join(unknown)),
                error_code=ErrorCode.TOOL_ERROR.value,
                message="unknown material ids",
            )

        state.draft = ScriptDraft(
            title=str(args.get("title") or ""),
            shots=shots,
            risks=[str(x) for x in (args.get("risks") or [])],
        )
        state.revision += 1
        # 草案变动后，旧的估算与规则结论一律作废，避免用过期结果 finish
        state.last_estimate = None
        state.last_rules = None

        chars = sum(len(s.narration) for s in shots)
        return ToolResult(
            tool="compose",
            ok=True,
            digest="已接收草案：{n} 个分镜，旁白共 {c} 字".format(n=len(shots), c=chars),
            data={"shots": len(shots), "narration_chars": chars},
        )

    def _estimate(self, state: LoopState) -> ToolResult:
        cfg = self.ctx.platform_cfg(state.task.platform)
        wps = float(cfg.get("words_per_second") or 4.5)
        r = duration.estimate(state.draft, wps, state.task.target_duration_s)
        if r.ok:
            for item in r.data.get("per_shot", []):
                for shot in state.draft.shots:
                    if shot.shot_id == item["shot_id"]:
                        shot.duration_s = item["duration_s"]
            passed = bool(r.data.get("passed"))
            state.last_estimate = {
                "total_s": r.data.get("total_s"),
                "target_s": r.data.get("target_s"),
                "passed": passed,
                "words_per_second": wps,
            }
            if not passed:
                state.fix_attempts += 1
        return r

    def _compress(self, state: LoopState) -> ToolResult:
        """确定性压缩：模型只决定"何时压"，算术与裁剪全部由工具完成。"""
        cfg = self.ctx.platform_cfg(state.task.platform)
        wps = float(cfg.get("words_per_second") or 4.5)
        r = compress_tool.compress(state.draft, state.task.target_duration_s, wps)
        if r.ok:
            for shot, item in zip(state.draft.shots, r.data.get("per_shot", [])):
                shot.narration = item["narration"]
                shot.duration_s = None
            state.revision += 1
            # 旁白变了，旧的估算与规则结论作废
            state.last_estimate = None
            state.last_rules = None
        return r

    def _check_rules(self, state: LoopState) -> ToolResult:
        r = rules.check(state.draft, self.ctx.rules, self.ctx)
        if r.ok:
            state.last_rules = {
                "passed": bool(r.data.get("passed")),
                "violations": r.data.get("violations", []),
            }
        return r
