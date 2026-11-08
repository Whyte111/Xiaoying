"""领域数据结构。只放数据，不放业务分支。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


MAX_TARGET_DURATION_S = 600.0


@dataclass
class TaskInput:
    topic: str = ""
    audience: str = ""
    platform: str = ""
    target_duration_s: float = 0.0
    style: str = ""
    material_ids: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "TaskInput":
        """严格解析。类型不对一律抛 ValueError，绝不静默变形。

        审计要点：material_ids 若传成字符串，`list("P01")` 会变成
        ["P","0","1"] 这种静默腐败，必须在这里挡住。
        """
        if not isinstance(d, dict):
            raise ValueError("输入必须是 JSON 对象")

        def as_text(key: str) -> str:
            value = d.get(key)
            if value is None:
                return ""
            if isinstance(value, (dict, list)):
                raise ValueError("{k} 必须是字符串".format(k=key))
            if isinstance(value, bool):
                raise ValueError("{k} 必须是字符串".format(k=key))
            return str(value).strip()

        raw_mids = d.get("material_ids")
        material_ids: List[str] = []
        if raw_mids is None:
            pass
        elif isinstance(raw_mids, list):
            for item in raw_mids:
                if isinstance(item, (dict, list)):
                    raise ValueError("material_ids 的元素必须是字符串")
                text = str(item).strip()
                if text:
                    material_ids.append(text)
        else:
            raise ValueError("material_ids 必须是数组")

        raw_duration = d.get("target_duration_s")
        if raw_duration is None:
            duration = 0.0
        elif isinstance(raw_duration, bool) or not isinstance(raw_duration, (int, float)):
            raise ValueError("target_duration_s 必须是数字")
        else:
            duration = float(raw_duration)
            if duration != duration:
                raise ValueError("target_duration_s 不能是 NaN")
            if duration in (float("inf"), float("-inf")):
                raise ValueError("target_duration_s 不能是 Infinity")

        return cls(
            topic=as_text("topic"),
            audience=as_text("audience"),
            platform=as_text("platform"),
            target_duration_s=duration,
            style=as_text("style"),
            material_ids=material_ids,
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def problems(self) -> List[str]:
        """返回所有不可运行的输入问题；空列表表示可以进入循环。"""
        items: List[str] = []
        if not self.topic:
            items.append("topic 不能为空")
        if not self.platform:
            items.append("platform 不能为空")
        if self.target_duration_s <= 0:
            items.append("target_duration_s 必须大于 0")
        elif self.target_duration_s > MAX_TARGET_DURATION_S:
            items.append(
                "target_duration_s 超过上限 {m} 秒".format(m=int(MAX_TARGET_DURATION_S))
            )
        return items

    def missing_required(self) -> List[str]:
        return self.problems()


@dataclass
class ActionDecision:
    """模型唯一能产出的东西。它不能调用工具，只能提议动作。"""

    action: str
    args: Dict[str, Any] = field(default_factory=dict)
    decision_basis: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ToolResult:
    tool: str
    ok: bool
    args: Dict[str, Any] = field(default_factory=dict)
    digest: str = ""
    data: Dict[str, Any] = field(default_factory=dict)
    error_code: Optional[str] = None
    message: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Observation:
    step: int
    action: str
    args: Dict[str, Any]
    result_digest: str
    accepted: bool
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Shot:
    shot_id: str
    narration: str = ""
    visual: str = ""
    subtitle: str = ""
    material_ids: List[str] = field(default_factory=list)
    # 事实溯源：每条产品声明必须指向某个素材的一条**原文事实**。
    # 结构形如 [{"material_id": "P01", "fact": "支持字幕"}]，
    # 由 rules 工具校验该 fact 必须精确存在于该素材，编造的能力表述无法伪造引用。
    fact_refs: List[Dict[str, str]] = field(default_factory=list)
    # duration_s 由 duration 工具写入，模型不可提供（B2）
    duration_s: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ScriptDraft:
    title: str = ""
    shots: List[Shot] = field(default_factory=list)
    risks: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def total_duration(self) -> float:
        return round(sum((s.duration_s or 0.0) for s in self.shots), 1)


@dataclass
class LoopState:
    task: TaskInput
    draft: Optional[ScriptDraft] = None
    observations: List[Observation] = field(default_factory=list)
    steps_used: int = 0
    tool_calls_used: int = 0
    searched: bool = False
    search_hit: bool = False
    search_hits: List[Dict[str, Any]] = field(default_factory=list)
    fix_attempts: int = 0
    revision: int = 0
    last_estimate: Optional[Dict[str, Any]] = None
    last_rules: Optional[Dict[str, Any]] = None
    duplicates: Dict[str, int] = field(default_factory=dict)


@dataclass
class RunResult:
    status: str
    reason: str = ""
    error_code: Optional[str] = None
    script: Optional[Dict[str, Any]] = None
    partial: Optional[Dict[str, Any]] = None
    steps_used: int = 0
    tool_calls_used: int = 0
    trace_path: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
