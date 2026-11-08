"""四个业务工具。全部返回 ToolResult，不向上抛异常。

纵深防御：即使上层门控失效，工具层也自己校验输入（例如素材必须存在）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List

from ..domain.models import Draft, Material, RiskFinding, Rule, Severity, Slot, ToolResult
from ..domain.rules import check_rules as _check_rules
from ..domain.slots import find_conflicts, suggest_alternatives
from ..domain.validation import (
    check_duplicate_ids,
    validate_existing,
    validate_material,
    validate_rule,
)
from ..errors import ConfigError
from ..store.json_store import JsonStore


def _read_json(path: Path) -> list:
    if not path.exists():
        raise ConfigError("缺少数据文件：%s" % path)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError("数据文件 %s 无法读取：%s" % (path, exc))
    except json.JSONDecodeError as exc:
        raise ConfigError("数据文件 %s 不是合法 JSON（第 %d 行第 %d 列）" % (path, exc.lineno, exc.colno))


class Toolbox:
    """工具上下文：数据目录 + store。所有依赖显式注入，便于替换与测试。"""

    def __init__(self, materials: List[Material], rules: List[Rule], existing, store: JsonStore) -> None:
        self.materials = materials
        self.rules = rules
        self.existing = existing
        self.store = store

    @staticmethod
    def load(data_dir: str, store_path: str) -> "Toolbox":
        base = Path(data_dir)
        if not base.is_dir():
            raise ConfigError("数据目录不存在：%s" % base)

        raw_materials = _read_json(base / "materials.json")
        raw_rules = _read_json(base / "rules.json")
        raw_slots = _read_json(base / "slots.json")

        if not isinstance(raw_materials, list) or not isinstance(raw_rules, list) or not isinstance(raw_slots, list):
            raise ConfigError("data 下的三个 JSON 根节点都必须是数组")

        try:
            materials = [validate_material(item, i + 1) for i, item in enumerate(raw_materials)]
            check_duplicate_ids(materials)
            rules = [validate_rule(item, i + 1) for i, item in enumerate(raw_rules)]
            existing = [validate_existing(item, i + 1) for i, item in enumerate(raw_slots)]
        except ValueError as exc:
            raise ConfigError(str(exc))

        return Toolbox(materials, rules, existing, JsonStore(store_path))

    def material_exists(self, material_id: str) -> bool:
        return any(m.id == material_id for m in self.materials)

    # ---------- 工具 1：读素材 ----------
    def read_material(self, material_id: str) -> ToolResult:
        try:
            if not isinstance(material_id, str) or not material_id.strip():
                return ToolResult.error("INVALID", "素材 ID 不能为空")
            for m in self.materials:
                if m.id == material_id:
                    return ToolResult(ok=True, data=m)
            return ToolResult.error("NOT_FOUND", "material not found: %s" % material_id)
        except Exception as exc:  # 工具不得抛异常
            return ToolResult.error("INVALID", str(exc))

    # ---------- 工具 2：规则检查 ----------
    def check_rules(self, material: Material) -> ToolResult:
        try:
            findings: List[RiskFinding] = _check_rules(material, self.rules)
            return ToolResult(ok=True, data=findings)
        except Exception as exc:
            return ToolResult.error("INVALID", str(exc))

    # ---------- 工具 3：时段查询 ----------
    def query_slots(self, slot: Slot) -> ToolResult:
        try:
            if not isinstance(slot, Slot):
                return ToolResult.error("INVALID", "时段参数非法")
            return ToolResult(ok=True, data=find_conflicts(slot, self.existing))
        except Exception as exc:
            return ToolResult.error("INVALID", str(exc))

    # ---------- 工具 4：保存草稿 ----------
    def save_draft(self, draft: Draft) -> ToolResult:
        try:
            # 边界：不允许给不存在的素材落草稿（防越权写入）
            if not self.material_exists(draft.material_id):
                return ToolResult.error("NOT_FOUND", "素材 %s 不在素材库，拒绝保存草稿" % draft.material_id)
            if draft.suggested_slot is None:
                return ToolResult.error("INVALID", "草稿缺少建议时段")
            outcome = self.store.upsert_draft(draft)
        except Exception as exc:
            return ToolResult.error("SAVE_FAIL", str(exc))
        if not outcome.ok:
            return ToolResult(ok=False, code="SAVE_FAIL", data=outcome, err=outcome.error)
        return ToolResult(ok=True, data=outcome)

    # ---------- 非工具：候选时段（domain 直调） ----------
    def suggest(self, slot: Slot, limit: int = 3) -> List[Slot]:
        return suggest_alternatives(slot, self.existing, limit=limit)
