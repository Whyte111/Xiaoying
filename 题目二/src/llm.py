"""LLM 决策器：OpenAI 兼容端点，输出严格 JSON 动作；任何异常退回 MockPolicy。"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, Optional

from .decision import DecisionPolicy, MockPolicy
from .types import ACTION_CALL_TOOL, ACTION_FINISH, ACTION_STOP, Action, Observation

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"

KNOWN_TOOLS = ("search_material", "generate_script", "estimate_cost", "save_plan")

SYSTEM_PROMPT = """你是内容任务规划 Agent 的决策器。每轮根据观察结果，只输出一个动作，JSON 格式，不要额外文字。

可用工具：
1. search_material  {"query": "关键词"}                  检索素材
2. generate_script  {"material_ids": ["m1"]}             基于素材生成脚本
3. estimate_cost    {"option_id": "A"}                   估算某个方案的成本
4. save_plan        {"topic": "...", "option_id": "A", "total_cost": 8.6, "script": {...}}  保存方案

输出格式（三选一）：
{"type": "call_tool", "tool": "工具名", "args": {...}, "reason": "一句话理由"}
{"type": "finish", "reason": "为什么可以结束"}
{"type": "stop", "reason": "为什么无法完成"}

决策规则：
- 按依赖推进：先检索素材，再生成脚本，再估算成本，最后保存。
- 若 violations 含 BUDGET_EXCEEDED，先尝试未用过的方案（tried_options 之外）；没有可选方案则 stop，并在 reason 里给出缺口。
- 若 last_tool_result.ok 为 false，不要原样重复上一次调用；换参数降级或换工具，同一工具失败两次就 stop。
- 只有 saved 为 true 时才能 finish。
- 金额单位与观察里的 budget 一致，不要自己编造数字。"""


class LLMDecisionPolicy(DecisionPolicy):
    name = "llm"

    def __init__(
        self,
        scenario: Dict[str, Any],
        fallback: Optional[DecisionPolicy] = None,
        transport: Optional[Callable[[Dict[str, Any]], str]] = None,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        timeout: float = 30.0,
        temperature: float = 0.0,
    ) -> None:
        self.scenario = scenario
        self.fallback = fallback or MockPolicy(scenario)
        self.base_url = (base_url or os.environ.get("LLM_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.api_key = api_key or os.environ.get("LLM_API_KEY", "")
        self.model = model or os.environ.get("LLM_MODEL") or DEFAULT_MODEL
        self.timeout = timeout
        self.temperature = temperature
        self.transport = transport or self._http
        self.last_source = "llm"
        self.last_error: Optional[str] = None
        self.calls = 0
        self.fallback_calls = 0

    # --- 接口 ---

    def next(self, obs: Observation) -> Action:
        self.calls += 1
        try:
            raw = self.transport(self._build_payload(obs))
            action = self._parse(raw)
        except Exception as exc:  # 网络、解析、校验失败一律降级
            self.last_error = f"{type(exc).__name__}: {exc}"
            self.last_source = "fallback"
            self.fallback_calls += 1
            return self.fallback.next(obs)
        self.last_error = None
        self.last_source = "llm"
        return action

    # --- 组装请求 ---

    def _build_payload(self, obs: Observation) -> Dict[str, Any]:
        return {
            "model": self.model,
            "temperature": self.temperature,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(self._observation(obs), ensure_ascii=False),
                },
            ],
        }

    @staticmethod
    def _observation(obs: Observation) -> Dict[str, Any]:
        return {
            "topic": obs.topic,
            "budget": obs.budget_total,
            "spent": obs.spent,
            "remaining_budget": round(obs.budget_total - obs.spent, 2),
            "materials": [{"id": m["id"], "title": m["title"]} for m in obs.materials],
            "script": {"script_id": obs.script.get("script_id")} if obs.script else None,
            "estimate": obs.estimate,
            "saved": obs.saved is not None,
            "tried_options": obs.tried_options,
            "attempts": obs.attempts,
            "violations": [{"code": v.code, "detail": v.detail} for v in obs.violations],
            "last_tool_result": (
                {
                    "tool": obs.last_result.tool,
                    "ok": obs.last_result.ok,
                    "error_code": obs.last_result.error_code,
                    "data": obs.last_result.data,
                }
                if obs.last_result
                else None
            ),
        }

    # --- 解析与校验 ---

    @staticmethod
    def _parse(text: str) -> Action:
        s = (text or "").strip()
        fence = re.match(r"^```[a-zA-Z]*\s*(.*?)\s*```$", s, re.S)
        if fence:
            s = fence.group(1).strip()
        try:
            data = json.loads(s)
        except json.JSONDecodeError as exc:
            raise ValueError(f"模型输出不是合法 JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError("模型输出必须是 JSON 对象")

        kind = data.get("type")
        reason = str(data.get("reason", ""))
        if kind == ACTION_CALL_TOOL:
            tool = data.get("tool")
            args = data.get("args") or {}
            if tool not in KNOWN_TOOLS:
                raise ValueError(f"模型选择了未知工具: {tool}")
            if not isinstance(args, dict):
                raise ValueError("args 必须是对象")
            return Action(ACTION_CALL_TOOL, tool, args, reason)
        if kind in (ACTION_FINISH, ACTION_STOP):
            return Action(kind, None, {}, reason)
        raise ValueError(f"未知动作类型: {kind}")

    # --- 默认传输 ---

    def _http(self, payload: Dict[str, Any]) -> str:
        url = self.base_url if self.base_url.endswith("/chat/completions") else self.base_url + "/chat/completions"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
        return data["choices"][0]["message"]["content"]
