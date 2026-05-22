"""
密码复杂度验证
"""

import re


def validate_password_strength(password: str) -> tuple[bool, str]:
    """
    验证密码强度。返回 (is_valid, error_message)。

    规则：
    - 最少 8 位
    - 必须包含大写字母
    - 必须包含小写字母
    - 必须包含数字
    - 必须包含特殊字符
    """
    if len(password) < 8:
        return False, "密码长度不能少于 8 位"

    if not re.search(r"[A-Z]", password):
        return False, "密码必须包含至少一个大写字母"

    if not re.search(r"[a-z]", password):
        return False, "密码必须包含至少一个小写字母"

    if not re.search(r"\d", password):
        return False, "密码必须包含至少一个数字"

    if not re.search(r"[!@#$%^&*()_+\-=\[\]{};':\"\\|,.<>/?`~]", password):
        return False, "密码必须包含至少一个特殊字符"

    return True, ""
