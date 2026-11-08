"""数据结构与枚举。只定义，不含逻辑。"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

# 终态
STATUS_COMPLETED = "completed"
STATUS_INCOMPLETE = "incomplete"
STATUS_STOPPED_BY_LIMIT = "stopped_by_limit"

# 动作
ACTION_CALL_TOOL = "call_tool"
ACTION_FINISH = "finish"
ACTION_STOP = "stop"

# 步骤状态
STEP_PENDING = "pending"
STEP_SUCCEEDED = "succeeded"
STEP_FAILED = "failed"
STEP_SKIPPED = "skipped"


@dataclass
class TaskInput:
    topic: str
    budget: Optional[float] = None
    deadline: Optional[str] = None


@dataclass
class Action:
    type: str
    tool: Optional[str] = None
    args: Dict[str, Any] = field(default_factory=dict)
    reason: str = ""


@dataclass
class ToolResult:
    ok: bool
    tool: str
    args: Dict[str, Any] = field(default_factory=dict)
    data: Dict[str, Any] = field(default_factory=dict)
    error_code: Optional[str] = None
    error_msg: Optional[str] = None
    cost: float = 0.0
    duration_ms: float = 0.0


@dataclass
class PlanItem:
    step_id: str
    name: str
    tool: str
    deps: List[str] = field(default_factory=list)
    status: str = STEP_PENDING
    est_cost: float = 0.0


@dataclass
class Violation:
    code: str
    detail: str


@dataclass
class Observation:
    """决策器唯一可见的输入。不含轮次依赖之外的历史。"""

    round: int
    topic: str
    budget_total: float
    spent: float
    time_left: float
    materials: List[Dict[str, Any]] = field(default_factory=list)
    script: Optional[Dict[str, Any]] = None
    estimate: Optional[Dict[str, Any]] = None
    saved: Optional[Dict[str, Any]] = None
    plan: List[PlanItem] = field(default_factory=list)
    tried_options: List[str] = field(default_factory=list)
    violations: List[Violation] = field(default_factory=list)
    attempts: Dict[str, int] = field(default_factory=dict)
    last_result: Optional[ToolResult] = None


@dataclass
class FinalResult:
    status: str
    topic: str
    budget: Optional[float]
    total_cost: float
    plan_cost: float
    tool_fee: float
    plan: List[PlanItem]
    tool_results: List[ToolResult]
    violations: List[Violation] = field(default_factory=list)
    saved_artifact: Optional[Dict[str, Any]] = None
    blocked_reason: Optional[str] = None
    rounds: int = 0
    tool_calls: int = 0
    trace_path: Optional[str] = None
    fingerprint: Optional[str] = None
    reused: bool = False
    time_checked: bool = False  # 时间可行性是否真的核算过（当前恒 False，见 docs）
    provider: str = "mock"  # 决策来源：mock / llm
    resumed: bool = False  # 是否从上次未完成的状态续跑
    fallback_calls: int = 0  # 决策降级到规则兜底的轮次数

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "FinalResult":
        return cls(
            status=d["status"],
            topic=d["topic"],
            budget=d.get("budget"),
            total_cost=d.get("total_cost", 0.0),
            plan_cost=d.get("plan_cost", 0.0),
            tool_fee=d.get("tool_fee", 0.0),
            plan=[PlanItem(**p) for p in d.get("plan", [])],
            tool_results=[ToolResult(**r) for r in d.get("tool_results", [])],
            violations=[Violation(**v) for v in d.get("violations", [])],
            saved_artifact=d.get("saved_artifact"),
            blocked_reason=d.get("blocked_reason"),
            rounds=d.get("rounds", 0),
            tool_calls=d.get("tool_calls", 0),
            trace_path=d.get("trace_path"),
            fingerprint=d.get("fingerprint"),
            reused=d.get("reused", False),
            time_checked=d.get("time_checked", False),
            provider=d.get("provider", "mock"),
            resumed=d.get("resumed", False),
            fallback_calls=d.get("fallback_calls", 0),
        )
