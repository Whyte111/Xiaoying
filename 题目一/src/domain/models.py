"""领域数据结构：只有定义，不含业务行为。"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional


class Severity(str, Enum):
    BLOCK = "block"
    WARN = "warn"

    def __str__(self) -> str:
        return self.value


class Action(str, Enum):
    FETCH = "fetch"
    CHECK = "check"
    QUERY = "query"
    RESCHEDULE = "reschedule"
    ASK = "ask"
    BLOCK = "block"
    SAVE = "save"
    END = "end"

    def __str__(self) -> str:
        return self.value


#: 只有这四个动作会真正调用工具
TOOL_NAMES: Dict[Action, str] = {
    Action.FETCH: "read_material",
    Action.CHECK: "check_rules",
    Action.QUERY: "query_slots",
    Action.SAVE: "save_draft",
}


class AuditStatus(str, Enum):
    PASS = "PASS"
    WARN = "WARN"
    NEED_INFO = "NEED_INFO"
    BLOCKED = "BLOCKED"

    def __str__(self) -> str:
        return self.value


class DraftStatus(str, Enum):
    NOT_SAVED = "NOT_SAVED"
    SAVED_CREATED = "SAVED_CREATED"
    SAVED_IDEMPOTENT = "SAVED_IDEMPOTENT"
    SAVED_UPDATED = "SAVED_UPDATED"
    FAILED = "FAILED"

    def __str__(self) -> str:
        return self.value


class RunStatus(str, Enum):
    DONE = "DONE"
    NEEDS_CONFIRM = "NEEDS_CONFIRM"
    NEEDS_INFO = "NEEDS_INFO"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    TOOL_FAILED = "TOOL_FAILED"
    INCOMPLETE = "INCOMPLETE"
    LIMIT_REACHED = "LIMIT_REACHED"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class Slot:
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        # 不变量：两端都带或都不带时区，且 end 必须晚于 start。
        # 这两个条件不满足时，重叠判定会静默出错（假阴性比崩溃更危险）。
        if not isinstance(self.start, datetime) or not isinstance(self.end, datetime):
            raise ValueError("Slot 的 start/end 必须是 datetime")
        if (self.start.tzinfo is None) != (self.end.tzinfo is None):
            raise ValueError("Slot 的 start/end 不能一个带时区、一个不带")
        if self.end <= self.start:
            raise ValueError("Slot 的 end 必须晚于 start：%s -> %s" % (self.start, self.end))

    @staticmethod
    def parse(text: str, minutes: int = 60) -> "Slot":
        start = datetime.fromisoformat(text)
        return Slot(start, start + timedelta(minutes=minutes))

    def to_dict(self) -> Dict[str, str]:
        return {"start": self.start.isoformat(), "end": self.end.isoformat()}

    @staticmethod
    def from_dict(data: Dict[str, str]) -> "Slot":
        if not isinstance(data, dict):
            raise ValueError("时段必须是对象")
        missing = [k for k in ("start", "end") if k not in data]
        if missing:
            raise ValueError("时段缺少字段：%s" % "、".join(missing))
        try:
            return Slot(datetime.fromisoformat(str(data["start"])), datetime.fromisoformat(str(data["end"])))
        except ValueError as exc:
            raise ValueError("时段格式非法（需 ISO 8601）：%s" % exc)

    def label(self) -> str:
        return "%s-%s" % (self.start.strftime("%Y-%m-%d %H:%M"), self.end.strftime("%H:%M"))


@dataclass
class Material:
    id: str
    title: Optional[str] = None
    body_ref: Optional[str] = None
    requested_slot: Optional[Slot] = None
    owner: Optional[str] = None

    #: 必填字段（骨架 §8 第 3 项）
    REQUIRED = ("id", "title", "requested_slot")

    def missing_fields(self) -> List[str]:
        out = []
        if not isinstance(self.id, str) or not self.id.strip():
            out.append("id")
        if not isinstance(self.title, str) or not self.title.strip():
            out.append("title")
        if not isinstance(self.requested_slot, Slot):
            out.append("requested_slot")
        return out

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "body_ref": self.body_ref,
            "requested_slot": self.requested_slot.to_dict() if self.requested_slot else None,
            "owner": self.owner,
        }

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "Material":
        rs = data.get("requested_slot")
        return Material(
            id=data.get("id") or "",
            title=data.get("title"),
            body_ref=data.get("body_ref"),
            requested_slot=Slot.from_dict(rs) if rs else None,
            owner=data.get("owner"),
        )


@dataclass
class Rule:
    id: str
    severity: Severity
    msg: str
    check: Dict[str, Any]  # {"field": "title", "op": "contains_any", "value": [...]}


@dataclass
class RiskFinding:
    rule_id: str
    severity: Severity
    msg: str
    field: str

    def to_dict(self) -> Dict[str, str]:
        return {"rule_id": self.rule_id, "severity": self.severity.value, "msg": self.msg, "field": self.field}


@dataclass
class Conflict:
    slot: Slot
    against_id: str
    kind: str = "overlap"


@dataclass
class PendingItem:
    kind: str  # missing_info | slot_change | risk_warn
    question: str
    blocks_save: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "question": self.question, "blocks_save": self.blocks_save}


@dataclass
class ExistingSlot:
    id: str
    slot: Slot
    owner: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "slot": self.slot.to_dict(), "owner": self.owner}

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "ExistingSlot":
        return ExistingSlot(data["id"], Slot.from_dict(data["slot"]), data.get("owner"))


@dataclass
class Draft:
    material_id: str
    audit_status: AuditStatus
    suggested_slot: Optional[Slot]
    pending: List[PendingItem] = field(default_factory=list)
    risk: List[RiskFinding] = field(default_factory=list)
    revision: int = 1
    created_at: str = ""
    updated_at: str = ""

    def signature(self) -> str:
        """幂等指纹：审核状态 + 建议时段 + 待确认项 + 风险集合。"""
        payload = {
            "audit": self.audit_status.value,
            "slot": self.suggested_slot.to_dict() if self.suggested_slot else None,
            "pending": sorted((p.kind + "|" + p.question) for p in self.pending),
            "risk": sorted((r.rule_id + "|" + r.msg) for r in self.risk),
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "material_id": self.material_id,
            "audit_status": self.audit_status.value,
            "suggested_slot": self.suggested_slot.to_dict() if self.suggested_slot else None,
            "pending": [p.to_dict() for p in self.pending],
            "risk": [r.to_dict() for r in self.risk],
            "revision": self.revision,
            "signature": self.signature(),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "Draft":
        rs = data.get("suggested_slot")
        return Draft(
            material_id=data["material_id"],
            audit_status=AuditStatus(data["audit_status"]),
            suggested_slot=Slot.from_dict(rs) if rs else None,
            pending=[PendingItem(**p) for p in data.get("pending", [])],
            risk=[RiskFinding(r["rule_id"], Severity(r["severity"]), r["msg"], r["field"]) for r in data.get("risk", [])],
            revision=data.get("revision", 1),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
        )


@dataclass
class SaveOutcome:
    kind: str  # created | idempotent | updated | failed
    draft: Optional[Draft] = None
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.kind != "failed"


@dataclass
class ToolResult:
    ok: bool
    code: str = "OK"
    data: Any = None
    err: Optional[str] = None

    @staticmethod
    def error(code: str, err: str) -> "ToolResult":
        return ToolResult(ok=False, code=code, data=None, err=err)


@dataclass
class Facts:
    """循环的事实集合：动作门控与最终结论都只依赖它。"""

    material: Optional[Material] = None
    material_complete: bool = False
    fetch_failed: bool = False
    no_alternative: bool = False
    save_failed: bool = False
    tool_error: bool = False
    tool_error_detail: str = ""
    rules_checked: bool = False
    findings: List[RiskFinding] = field(default_factory=list)
    conflicts_checked: bool = False
    conflicts: List[Conflict] = field(default_factory=list)
    candidate: Optional[Slot] = None
    pending: List[PendingItem] = field(default_factory=list)
    asked: bool = False
    blocked: bool = False
    draft: Optional[Draft] = None
    save_kind: str = ""
    notes: List[str] = field(default_factory=list)

    @property
    def has_block(self) -> bool:
        return any(f.severity is Severity.BLOCK for f in self.findings)


@dataclass
class StepRecord:
    step: int
    action: str
    tool: Optional[str]
    args: Dict[str, Any]
    result_summary: str
    rationale: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step": self.step,
            "action": self.action,
            "tool": self.tool,
            "args": self.args,
            "result_summary": self.result_summary,
            "rationale": self.rationale,
        }


@dataclass
class ItemResult:
    material_id: str
    audit_status: AuditStatus
    risk_reasons: List[str]
    suggested_slot: Optional[Slot]
    pending_items: List[PendingItem]
    draft_status: DraftStatus
    run_status: RunStatus
    message: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "material_id": self.material_id,
            "audit_status": self.audit_status.value,
            "risk_reasons": list(self.risk_reasons),
            "suggested_slot": self.suggested_slot.label() if self.suggested_slot else None,
            "pending_items": [p.to_dict() for p in self.pending_items],
            "draft_status": self.draft_status.value,
        }


@dataclass
class RunResult:
    status: RunStatus
    item: ItemResult
    steps: List[StepRecord] = field(default_factory=list)
    trace_path: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status.value,
            "item": self.item.to_dict(),
            "steps": [s.to_dict() for s in self.steps],
            "trace_path": self.trace_path,
        }
