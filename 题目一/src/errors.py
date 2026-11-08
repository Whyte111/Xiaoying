"""可对用户展示的错误类型：CLI 捕获后只打印一行，不甩堆栈。"""


class AppError(Exception):
    """所有"能解释清楚、应友好提示"的错误的基类。"""


class ConfigError(AppError):
    """数据文件缺失、损坏或格式非法。"""


class StoreError(AppError):
    """草稿库无法读取或写入。"""
