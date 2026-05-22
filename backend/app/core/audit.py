"""
审计日志 + 密钥脱敏

借鉴 OpenClaw io.audit.ts + crestodian/audit.ts

核心思路：
- JSONL 格式审计日志（每行一条 JSON 记录）
- 密钥字段自动脱敏（password/token/api_key/credential 等）
- 按日期滚动文件
- 记录关键操作：登录、配置变更、Agent CRUD、Skill CRUD、工具调用等
"""

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 密钥 CLI 标志后缀模式（借鉴 OpenClaw io.audit.ts SECRET_FLAG_SUFFIX_PATTERN）
SECRET_KEY_PATTERNS = re.compile(
    r"(?:password|passwd|secret|token|api[-_]?key|auth|credential|private[-_]?key|access[-_]?key)",
    re.IGNORECASE,
)

# 完全脱敏的关键词（值替换为 [REDACTED]）
FULL_REDACT_KEYWORDS = {
    "password",
    "secret",
    "token",
    "api_key",
    "credential",
    "private_key",
    "access_key",
}


class AuditLogger:
    """JSONL 审计日志记录器

    借鉴 OpenClaw io.audit.ts + crestodian/audit.ts

    特性：
    - JSONL 格式（每行一条 JSON）
    - 密钥字段自动脱敏
    - 按日期滚动文件（audit-YYYYMMDD.jsonl）
    - 异常安全（写入失败不中断主流程）
    """

    def __init__(self, log_dir: str = "~/.pioneclaw/audit"):
        self.log_path = Path(log_dir).expanduser()
        self.log_path.mkdir(parents=True, exist_ok=True)

    def _current_file(self) -> Path:
        """当前日志文件（按日期滚动）"""
        return self.log_path / f"audit-{datetime.now():%Y%m%d}.jsonl"

    def log(
        self,
        action: str,
        actor: str,
        resource: str = "",
        details: dict[str, Any] | None = None,
        sensitive_args: dict[str, Any] | None = None,
    ) -> None:
        """记录审计条目

        Args:
            action: 操作类型（create/update/delete/execute/login/config_change）
            actor: 操作者（用户ID 或 "system"）
            resource: 资源标识（如 "agent:abc123"）
            details: 操作详情（非敏感）
            sensitive_args: 可能包含敏感信息的参数（会自动脱敏）
        """
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "actor": actor,
            "resource": resource,
            "details": details or {},
        }

        # 脱敏处理
        if sensitive_args:
            entry["details"].update(self._redact_secrets(sensitive_args))

        try:
            with open(self._current_file(), "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.warning(f"Failed to write audit log: {e}")

    def log_login(self, user_id: str, success: bool, ip: str = "") -> None:
        """记录登录事件"""
        self.log(
            action="login" if success else "login_failed",
            actor=user_id,
            details={"success": success, "ip": ip},
        )

    def log_config_change(
        self, actor: str, config_key: str, old_value: Any = None, new_value: Any = None
    ) -> None:
        """记录配置变更"""
        self.log(
            action="config_change",
            actor=actor,
            resource=f"config:{config_key}",
            sensitive_args={"old_value": old_value, "new_value": new_value},
        )

    def log_agent_action(
        self, action: str, actor: str, agent_id: str, details: dict | None = None
    ) -> None:
        """记录 Agent 操作"""
        self.log(
            action=f"agent_{action}",
            actor=actor,
            resource=f"agent:{agent_id}",
            details=details,
        )

    def log_tool_execute(
        self, actor: str, tool_name: str, allowed: bool, agent_id: str = ""
    ) -> None:
        """记录工具调用"""
        self.log(
            action="tool_execute" if allowed else "tool_blocked",
            actor=actor,
            resource=f"tool:{tool_name}",
            details={"allowed": allowed, "agent_id": agent_id},
        )

    def _redact_secrets(self, data: dict[str, Any]) -> dict[str, Any]:
        """自动脱敏密钥字段"""
        redacted = {}
        for key, value in data.items():
            if self._is_secret_key(key):
                redacted[key] = "[REDACTED]"
            elif isinstance(value, str) and len(value) > 8:
                redacted[key] = value[:4] + "****"
            else:
                redacted[key] = value
        return redacted

    def _is_secret_key(self, key: str) -> bool:
        """判断 key 是否为密钥字段"""
        key_lower = key.lower().replace("-", "_")
        # 精确匹配完全脱敏关键词
        if key_lower in FULL_REDACT_KEYWORDS:
            return True
        # 模式匹配
        return bool(SECRET_KEY_PATTERNS.search(key_lower))

    def read_logs(
        self, date: str | None = None, action: str | None = None, limit: int = 100
    ) -> list[dict]:
        """读取审计日志

        Args:
            date: 日期字符串（YYYYMMDD），默认今天
            action: 过滤操作类型
            limit: 最大返回条数
        """
        if date:
            file_path = self.log_path / f"audit-{date}.jsonl"
        else:
            file_path = self._current_file()

        if not file_path.exists():
            return []

        entries = []
        try:
            with open(file_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        if action and entry.get("action") != action:
                            continue
                        entries.append(entry)
                    except json.JSONDecodeError:
                        continue
        except Exception as e:
            logger.warning(f"Failed to read audit log: {e}")

        return entries[-limit:]


# 全局实例
audit = AuditLogger()
