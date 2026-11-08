"""数据校验：把"坏数据"挡在进入内核之前，错误信息带文件名与第几条。"""
from __future__ import annotations

from typing import Any, Dict, List

from .models import ExistingSlot, Material, Rule, Severity, Slot


class DataError(ValueError):
    """单条数据非法。"""


def validate_material(raw: Any, index: int) -> Material:
    where = "materials.json 第 %d 条" % index
    if not isinstance(raw, dict):
        raise DataError("%s 不是对象" % where)

    material_id = raw.get("id")
    if not isinstance(material_id, str) or not material_id.strip():
        raise DataError("%s 缺少合法 id" % where)

    title = raw.get("title")
    if title is not None and not isinstance(title, str):
        raise DataError("素材 %s 的 title 必须是字符串，实际是 %s" % (material_id, type(title).__name__))

    body_ref = raw.get("body_ref")
    if body_ref is not None and not isinstance(body_ref, str):
        raise DataError("素材 %s 的 body_ref 必须是字符串" % material_id)

    owner = raw.get("owner")
    if owner is not None and not isinstance(owner, str):
        raise DataError("素材 %s 的 owner 必须是字符串" % material_id)

    slot = None
    if raw.get("requested_slot") is not None:
        try:
            slot = Slot.from_dict(raw["requested_slot"])
        except ValueError as exc:
            raise DataError("素材 %s 的 requested_slot 非法：%s" % (material_id, exc))

    return Material(id=material_id.strip(), title=title, body_ref=body_ref, requested_slot=slot, owner=owner)


def validate_rule(raw: Any, index: int) -> Rule:
    where = "rules.json 第 %d 条" % index
    if not isinstance(raw, dict):
        raise DataError("%s 不是对象" % where)
    rule_id = raw.get("id")
    if not isinstance(rule_id, str) or not rule_id.strip():
        raise DataError("%s 缺少合法 id" % where)
    try:
        severity = Severity(str(raw.get("severity")))
    except ValueError:
        raise DataError("规则 %s 的 severity 只能是 block 或 warn" % rule_id)
    msg = raw.get("msg")
    if not isinstance(msg, str) or not msg.strip():
        raise DataError("规则 %s 缺少 msg" % rule_id)
    check = raw.get("check")
    if not isinstance(check, dict) or "op" not in check:
        raise DataError("规则 %s 的 check 必须是含 op 的对象" % rule_id)
    return Rule(rule_id.strip(), severity, msg, check)


def validate_existing(raw: Any, index: int) -> ExistingSlot:
    where = "slots.json 第 %d 条" % index
    if not isinstance(raw, dict):
        raise DataError("%s 不是对象" % where)
    slot_id = raw.get("id")
    if not isinstance(slot_id, str) or not slot_id.strip():
        raise DataError("%s 缺少合法 id" % where)
    if raw.get("slot") is None:
        raise DataError("%s 缺少 slot" % where)
    try:
        slot = Slot.from_dict(raw["slot"])
    except ValueError as exc:
        raise DataError("%s 的 slot 非法：%s" % (where, exc))
    return ExistingSlot(slot_id.strip(), slot, raw.get("owner"))


def check_duplicate_ids(materials: List[Material]) -> None:
    seen: Dict[str, int] = {}
    for material in materials:
        seen[material.id] = seen.get(material.id, 0) + 1
    duplicated = [mid for mid, count in seen.items() if count > 1]
    if duplicated:
        raise DataError("materials.json 存在重复素材 ID：%s" % "、".join(sorted(duplicated)))
