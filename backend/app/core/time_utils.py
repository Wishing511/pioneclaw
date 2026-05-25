"""时间工具函数 - 安全的 datetime 序列化"""

from datetime import datetime, timezone


def format_dt(dt: datetime | None) -> str | None:
    """将 datetime 序列化为带 UTC 偏移的 ISO 格式字符串。

    - 若 dt 为 None，返回 None
    - 若 dt 无时区（naive），视为 UTC 并附加 +00:00
    - 若 dt 已有 tzinfo，保持原时区直接 isoformat()
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()
