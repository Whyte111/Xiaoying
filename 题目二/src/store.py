"""只负责读数据与最小校验，不解释业务含义。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from . import config

_REQUIRED_KEYS = ("materials", "fee_table", "plan_options", "script_template")
_REQUIRED_TOOLS = ("search_material", "generate_script", "estimate_cost", "save_plan")


def _need(cond: bool, message: str) -> None:
    if not cond:
        raise ValueError(f"场景数据不合法: {message}")


def validate_scenario(data: Any) -> Dict[str, Any]:
    """校验嵌套结构与类型，失败时给出可定位的错误。"""
    _need(isinstance(data, dict), "顶层必须是对象")
    missing = [k for k in _REQUIRED_KEYS if k not in data]
    _need(not missing, f"缺少字段 {missing}")

    materials = data["materials"]
    _need(isinstance(materials, list), "materials 必须是数组")
    for i, m in enumerate(materials):
        _need(isinstance(m, dict), f"materials[{i}] 必须是对象")
        _need(isinstance(m.get("id"), str) and m["id"], f"materials[{i}].id 缺失或非字符串")
        _need(isinstance(m.get("title"), str), f"materials[{i}].title 缺失或非字符串")
        if "tags" in m:
            _need(isinstance(m["tags"], list), f"materials[{i}].tags 必须是数组")

    tpl = data["script_template"]
    _need(isinstance(tpl, dict), "script_template 必须是对象")
    for key in ("title_prefix", "est_cost", "est_duration"):
        _need(key in tpl, f"script_template.{key} 缺失")
    _need(isinstance(tpl["est_cost"], (int, float)), "script_template.est_cost 必须是数字")
    _need(isinstance(tpl["est_duration"], (int, float)), "script_template.est_duration 必须是数字")

    fee = data["fee_table"]
    _need(isinstance(fee, dict), "fee_table 必须是对象")
    for tool in _REQUIRED_TOOLS:
        _need(tool in fee, f"fee_table 缺少工具 {tool}")
        entry = fee[tool]
        _need(isinstance(entry, dict), f"fee_table.{tool} 必须是对象")
        _need(isinstance(entry.get("cost"), (int, float)), f"fee_table.{tool}.cost 必须是数字")

    options = data["plan_options"]
    _need(isinstance(options, list) and options, "plan_options 必须是非空数组")
    seen = set()
    for i, opt in enumerate(options):
        _need(isinstance(opt, dict), f"plan_options[{i}] 必须是对象")
        _need(isinstance(opt.get("id"), str) and opt["id"], f"plan_options[{i}].id 缺失或非字符串")
        _need(opt["id"] not in seen, f"plan_options 存在重复 id {opt['id']}")
        seen.add(opt["id"])
        _need(isinstance(opt.get("est_cost"), (int, float)), f"plan_options[{i}].est_cost 必须是数字")
        _need(isinstance(opt.get("items"), list), f"plan_options[{i}].items 必须是数组")
    return data


def load_scenario(path: Optional[Path] = None) -> Dict[str, Any]:
    p = Path(path) if path else config.DEFAULT_SCENARIO
    if not p.exists():
        raise FileNotFoundError(f"找不到数据文件: {p}")
    try:
        with p.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as exc:
        raise ValueError(f"场景数据不是合法 JSON: {exc}") from exc
    return validate_scenario(data)


def get_option(scenario: Dict[str, Any], option_id: str) -> Optional[Dict[str, Any]]:
    for opt in scenario["plan_options"]:
        if opt["id"] == option_id:
            return opt
    return None


def find_materials(scenario: Dict[str, Any], query: str) -> list:
    """按关键词在标题与标签里做包含匹配；空 query 返回全部。"""
    q = (query or "").strip()
    if not q:
        return list(scenario["materials"])
    hits = []
    for m in scenario["materials"]:
        blob = m["title"] + " " + " ".join(m.get("tags", []))
        if q in blob:
            hits.append(m)
    return hits
