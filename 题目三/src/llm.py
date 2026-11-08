"""LLM 客户端：DeepSeek(OpenAI 兼容) 实现 + 离线 Fake 实现。零第三方依赖。"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Dict, List, Protocol

from .config import Config


class LLMError(RuntimeError):
    """模型调用失败（网络、HTTP、返回体非法），由 agent 转成 TOOL_ERROR 状态。"""


class LLMClient(Protocol):
    def complete(self, messages: "List[Dict[str, str]]") -> "Dict[str, Any]": ...


class DeepSeekClient:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg

    def complete(self, messages: "List[Dict[str, str]]") -> "Dict[str, Any]":
        if not self.cfg.api_key:
            raise LLMError("缺少 LLM_API_KEY，无法在线调用；请使用 --offline")
        body = {
            "model": self.cfg.model,
            "messages": messages,
            "temperature": 0.0,
            "response_format": {"type": "json_object"},
            "stream": False,
        }
        req = urllib.request.Request(
            self.cfg.base_url.rstrip("/") + "/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + self.cfg.api_key,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.cfg.timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "ignore")[:300]
            raise LLMError(f"HTTP {e.code}: {self.cfg.redact(detail)}") from e
        except Exception as e:  # 网络/超时
            raise LLMError(self.cfg.redact(f"{type(e).__name__}: {e}")) from e

        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as e:
            raise LLMError(f"返回体结构异常: {str(payload)[:200]}") from e
        return self._parse(content)

    @staticmethod
    def _parse(content: str) -> "Dict[str, Any]":
        text = content.strip()
        if text.startswith("```"):
            text = text.strip("`")
            text = text.split("\n", 1)[1] if "\n" in text else text
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise LLMError(f"模型未返回合法 JSON: {text[:200]}") from e
        if not isinstance(data, dict):
            raise LLMError("模型返回的不是 JSON 对象")
        return data


class FakeLLM:
    """按脚本逐条回放决策，用于离线验证流程与测试；不证明真实模型效果。"""

    def __init__(self, script: "List[Dict[str, Any]]") -> None:
        self.script = list(script)
        self.cursor = 0
        self.calls: "List[List[Dict[str, str]]]" = []

    def complete(self, messages: "List[Dict[str, str]]") -> "Dict[str, Any]":
        self.calls.append(messages)
        if self.cursor >= len(self.script):
            raise LLMError("FakeLLM 脚本已耗尽")
        step = self.script[self.cursor]
        self.cursor += 1
        return step
