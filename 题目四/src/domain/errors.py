"""错误码与终态枚举。错误码同时是 5 个验收场景的判定锚点。"""

from enum import Enum


class ErrorCode(str, Enum):
    MISSING_INPUT = "MISSING_INPUT"
    SEARCH_EMPTY = "SEARCH_EMPTY"
    TOOL_ERROR = "TOOL_ERROR"
    DURATION_EXCEEDED = "DURATION_EXCEEDED"
    RULE_VIOLATION = "RULE_VIOLATION"
    STEP_LIMIT = "STEP_LIMIT"
    UNSATISFIABLE = "UNSATISFIABLE"
    MODEL_ERROR = "MODEL_ERROR"


class Status(str, Enum):
    COMPLETED = "completed"
    INCOMPLETE = "incomplete"
    NEEDS_INPUT = "needs_input"
    FAILED = "failed"
