"""配置常量。不含业务逻辑。"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
OUT_DIR = ROOT / "out"

# 执行上限（骨架第 6 节：步数 + 调用数双上限）
MAX_STEPS = 12
MAX_TOOL_CALLS = 8
TOOL_TIMEOUT_S = 5.0

DEFAULT_SCENARIO = DATA_DIR / "scenario.json"


def display_path(path) -> str:
    """对外展示/落盘的路径：仓库内用相对路径，保证换机器可读。"""
    p = Path(path)
    try:
        return p.resolve().relative_to(ROOT).as_posix()
    except (ValueError, OSError):
        return str(p)


def load_env(path: Path = None) -> None:
    """读取 .env（KEY=VALUE），已存在的环境变量优先。不打印任何值。"""
    p = Path(path) if path else ROOT / ".env"
    if not p.exists():
        return
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value

