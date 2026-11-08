"""模型客户端接口。

边界：模型只能产出 ActionDecision，不能执行工具、不能改 state、不能写文件。
真实模型与离线 Mock 必须实现同一接口，走同一条循环。
"""

from __future__ import annotations

from typing import Protocol

from ..domain.schemas import ActionDecision, LoopState


class ModelError(RuntimeError):
    """模型调用失败（网络/格式/密钥）。不包含任何密钥内容。"""


class ModelClient(Protocol):
    name: str

    def decide(self, state: LoopState) -> ActionDecision:
        ...
