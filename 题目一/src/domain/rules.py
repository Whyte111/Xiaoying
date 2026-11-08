"""规则检查：只判不修，纯函数。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .models import Material, RiskFinding, Rule, Severity


def _field_values(material: Material, field_name: str) -> List[Any]:
    if field_name == "title":
        return [material.title]
    if field_name == "body_ref":
        return [material.body_ref]
    if field_name == "owner":
        return [material.owner]
    if field_name == "requested_slot":
        return [material.requested_slot]
    return [None]


def _match(op: str, values: List[Any], expected: Optional[Any]) -> bool:
    if op == "missing":
        return all(v is None or (isinstance(v, str) and not v.strip()) for v in values)
    if op == "present":
        return any(v is not None and (not isinstance(v, str) or v.strip()) for v in values)
    text = " ".join(v for v in values if isinstance(v, str))
    if op == "contains_any":
        return any(word in text for word in (expected or []))
    if op == "max_len":
        return any(isinstance(v, str) and len(v) > int(expected) for v in values)
    if op == "min_len":
        return any(isinstance(v, str) and len(v) < int(expected) for v in values)
    raise ValueError("unsupported rule op: %s" % op)


def check_rules(material: Material, rules: List[Rule]) -> List[RiskFinding]:
    findings: List[RiskFinding] = []
    for rule in rules:
        spec: Dict[str, Any] = rule.check
        field_name = spec.get("field", "title")
        if _match(spec["op"], _field_values(material, field_name), spec.get("value")):
            findings.append(RiskFinding(rule.id, rule.severity, rule.msg, field_name))
    return findings


def has_block(findings: List[RiskFinding]) -> bool:
    return any(f.severity is Severity.BLOCK for f in findings)
