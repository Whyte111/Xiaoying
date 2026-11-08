"""DeepSeek 真实模型客户端（零第三方依赖，只用标准库）。

边界：
- 密钥只从环境变量或项目根的 .env 读取，**不写入代码、日志或 trace**；
- 本模块是全系统唯一对外发出网络请求的地方；
- 模型只返回 ActionDecision，不执行任何工具。
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from ..config import ROOT
from ..domain.schemas import ActionDecision, LoopState
from .base import ModelError
from ..agent.prompts import build_messages

_KNOWN_ACTIONS = {
    "search",
    "rewrite_query",
    "ask_clarify",
    "compose",
    "estimate",
    "check_rules",
    "finish",
}

# 白名单：只从 .env 吸收这几个键，避免任意键注入进程环境
_ENV_WHITELIST = {
    "LLM_API_KEY",
    "LLM_BASE_URL",
    "LLM_MODEL",
    "DEEPSEEK_API_KEY",
    "DEEPSEEK_BASE_URL",
    "DEEPSEEK_MODEL",
}


def _env(*names: str) -> str:
    """按顺序取第一个非空环境变量。只取值，不回显。"""
    for name in names:
        value = os.environ.get(name)
        if value and value.strip():
            return value.strip()
    return ""


def _ensure_env() -> None:
    """若环境变量缺失，尝试从项目根 .env 读取。只读入内存，不回显。"""
    if _env("LLM_API_KEY", "DEEPSEEK_API_KEY"):
        return
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    try:
        text = env_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key in _ENV_WHITELIST and value and key not in os.environ:
            os.environ[key] = value


def _parse_decision(content: str) -> Optional[ActionDecision]:
    text = (content or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        obj = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None

    action = str(obj.get("action") or "").strip()
    if not action:
        return None
    args = obj.get("args")
    if not isinstance(args, dict):
        args = {}
    basis = str(obj.get("decision_basis") or "").strip()
    if action not in _KNOWN_ACTIONS:
        # 交给 policy 驳回，保持“模型可犯错、由护栏兜底”的结构
        basis = basis or "模型给出了白名单外的动作"
    return ActionDecision(action=action, args=args, decision_basis=basis)


class DeepSeekModel:
    name = "deepseek"

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: int = 90,
    ) -> None:
        _ensure_env()
        self.api_key = _env("LLM_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY")
        if not self.api_key:
            raise ModelError("缺少 LLM_API_KEY：请在 .env 中配置，或用 --model mock 离线运行")
        self.model = _env("LLM_MODEL", "DEEPSEEK_MODEL") or "deepseek-chat"
        base = _env("LLM_BASE_URL", "DEEPSEEK_BASE_URL") or "https://api.deepseek.com"
        self.base_url = base.rstrip("/")
        self.timeout = timeout

    # ---- ModelClient 接口 ----
    def decide(self, state: LoopState) -> ActionDecision:
        messages = build_messages(state)
        bad_output = ""
        for _ in range(2):  # 首次 + 一次格式纠错
            content = self._chat(messages)
            decision = _parse_decision(content)
            if decision is not None:
                return decision
            bad_output = content[:300]
            messages = [
                messages[0],
                messages[1],
                {"role": "assistant", "content": content},
                {
                    "role": "user",
                    "content": "上面的输出不是合法 JSON 对象。请只输出一个 JSON 对象，不要解释、不要代码围栏。",
                },
            ]
        raise ModelError("模型连续返回非法格式（已重试 1 次）：" + bad_output)

    # ---- 内部 ----
    # 仅这些状态码属于“可能自己恢复”的瞬时故障，值得重试一次
    _RETRYABLE_STATUS = (429, 500, 502, 503, 504)

    def _chat(self, messages: List[Dict[str, str]]) -> str:
        last_error = "unknown"
        for attempt in range(2):
            try:
                return self._post(messages)
            except urllib.error.HTTPError as exc:
                if exc.code in self._RETRYABLE_STATUS and attempt == 0:
                    last_error = "HTTP {c}".format(c=exc.code)
                    time.sleep(1.5)
                    continue
                raise ModelError("模型返回 HTTP {c}".format(c=exc.code))
            except urllib.error.URLError as exc:
                if attempt == 0:
                    last_error = str(exc.reason)
                    time.sleep(1.5)
                    continue
                raise ModelError("无法连接模型服务：{r}".format(r=exc.reason))
        raise ModelError("模型服务连续失败：{e}".format(e=last_error))

    def _post(self, messages: List[Dict[str, str]]) -> str:
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=data,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + self.api_key,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ModelError("模型返回内容无法解析为 JSON")

        try:
            return body["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            raise ModelError("模型返回结构异常")
