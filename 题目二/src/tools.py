"""四个业务工具。纯函数式：只通过入参拿数据，除 save_plan 外无副作用。"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional, Set

from . import config
from .store import find_materials, get_option
from .types import ToolResult

# 步骤号 -> 工具名，供 FAIL_STEP 环境变量使用（见 系统骨架.md 第 7 节）
STEP_TO_TOOL = {
    "s1": "search_material",
    "s2": "generate_script",
    "s3": "estimate_cost",
    "s4": "save_plan",
}


# 有副作用的工具：超时后无法判断是否已生效，不能按普通失败重试
SIDE_EFFECT_TOOLS = {"save_plan"}


def _parse_fail_steps() -> Set[str]:
    raw = os.environ.get("FAIL_STEP", "").strip()
    out: Set[str] = set()
    for part in raw.replace(";", ",").split(","):
        token = part.strip()
        if token:
            out.add(STEP_TO_TOOL.get(token, token))
    return out


class Toolbox:
    def __init__(
        self,
        scenario: Dict[str, Any],
        out_dir: Optional[Path] = None,
        timeout_s: Optional[float] = None,
    ) -> None:
        self.scenario = scenario
        self.out_dir = Path(out_dir) if out_dir else config.OUT_DIR
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.fail_tools = _parse_fail_steps()
        self._injected_used: Set[str] = set()
        self.timeout_s = float(config.TOOL_TIMEOUT_S if timeout_s is None else timeout_s)

    def call(self, name: str, args: Dict[str, Any]) -> ToolResult:
        fee = self.scenario["fee_table"].get(name, {"cost": 0.0, "duration": 0.0})
        # 失败注入：每个工具只失败一次，用于复现"注入一次失败"
        if name in self.fail_tools and name not in self._injected_used:
            self._injected_used.add(name)
            return _err(name, args, "INJECTED_FAILURE", f"注入的故障：{name} 调用失败", fee)
        started = time.perf_counter()
        try:
            handler = getattr(self, "_" + name)
        except AttributeError:
            return _err(name, args, "UNKNOWN_TOOL", f"未知工具: {name}", fee)
        try:
            data = handler(args or {})
        except _ToolError as exc:
            return _err(name, args, exc.code, exc.message, fee)
        except Exception as exc:  # 兜底，保证不会把异常抛穿循环
            return _err(name, args, "TOOL_ERROR", f"{type(exc).__name__}: {exc}", fee)
        elapsed = (time.perf_counter() - started) * 1000
        # 同步工具无法抢占式中断：超时在执行结束后判定，并作失败处理（见 docs）
        if self.timeout_s > 0 and elapsed > self.timeout_s * 1000:
            unknown = name in SIDE_EFFECT_TOOLS
            return ToolResult(
                ok=False,
                tool=name,
                args=args,
                error_code="OUTCOME_UNKNOWN" if unknown else "TIMEOUT",
                error_msg=(
                    f"工具耗时 {elapsed:.0f}ms 超过上限 {self.timeout_s * 1000:.0f}ms，"
                    + ("已产生副作用，结果未知" if unknown else "按失败处理")
                ),
                cost=float(fee.get("cost", 0.0)),
                duration_ms=round(elapsed, 2),
            )
        return ToolResult(
            ok=True,
            tool=name,
            args=args,
            data=data,
            cost=float(fee.get("cost", 0.0)),
            duration_ms=round(elapsed, 2),
        )

    # --- 工具实现 ---

    def _search_material(self, args: Dict[str, Any]) -> Dict[str, Any]:
        hits = find_materials(self.scenario, str(args.get("query", "")))
        return {"count": len(hits), "materials": hits}

    def _generate_script(self, args: Dict[str, Any]) -> Dict[str, Any]:
        ids = args.get("material_ids") or []
        by_id = {m["id"]: m for m in self.scenario["materials"]}
        picked = [by_id[i] for i in ids if i in by_id]
        if not picked:
            raise _ToolError("EMPTY_MATERIALS", "没有可用素材，无法生成脚本")
        tpl = self.scenario["script_template"]
        # 确定性 ID：同一组素材永远得到同一个脚本号，便于复现与核对
        digest = hashlib.sha1(",".join(sorted(ids)).encode("utf-8")).hexdigest()[:6]
        return {
            "script_id": "sc-" + digest,
            "title": tpl["title_prefix"] + "、".join(m["title"] for m in picked),
            "based_on": [m["id"] for m in picked],
            "est_cost": float(tpl["est_cost"]),
            "est_duration": float(tpl["est_duration"]),
        }

    def _estimate_cost(self, args: Dict[str, Any]) -> Dict[str, Any]:
        option_id = str(args.get("option_id", ""))
        opt = get_option(self.scenario, option_id)
        if opt is None:
            raise _ToolError("UNKNOWN_OPTION", f"未知方案: {option_id}")
        return {
            "option_id": opt["id"],
            "name": opt["name"],
            "items": list(opt["items"]),
            "total_cost": float(opt["est_cost"]),
            "est_duration": float(opt["est_duration"]),
        }

    def _save_plan(self, args: Dict[str, Any]) -> Dict[str, Any]:
        option_id = str(args.get("option_id", ""))
        opt = get_option(self.scenario, option_id)
        if opt is None:
            raise _ToolError("UNKNOWN_OPTION", f"未知方案: {option_id}")
        artifact_id = "plan-" + hashlib.sha1(
            f"{args.get('topic', '')}|{option_id}".encode("utf-8")
        ).hexdigest()[:8]
        payload = {
            "artifact_id": artifact_id,
            "topic": args.get("topic", ""),
            "option": opt,
            "script": args.get("script"),
            "plan_cost": float(opt["est_cost"]),
            "total_cost": float(args.get("total_cost", opt["est_cost"])),
        }
        path = self.out_dir / f"{artifact_id}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return {
            "artifact_id": artifact_id,
            "path": config.display_path(path),
            "option_id": option_id,
        }


class _ToolError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _err(tool: str, args: Dict[str, Any], code: str, msg: str, fee: Dict[str, Any]) -> ToolResult:
    return ToolResult(
        ok=False,
        tool=tool,
        args=args or {},
        error_code=code,
        error_msg=msg,
        cost=float(fee.get("cost", 0.0)),
        duration_ms=0.0,
    )
