"""
阶段 Y 测试 — 工具级沙箱策略 + 审计日志

覆盖：
- ToolPolicyConfig / ToolPolicy / resolve_tool_policy
- AuditLogger（JSONL格式、密钥脱敏、日期滚动、读取日志）
- 便捷方法（log_login, log_config_change, log_agent_action, log_tool_execute）
"""

import json
import tempfile

from app.core.audit import SECRET_KEY_PATTERNS, AuditLogger
from app.core.sandbox_policy import (
    ToolPolicy,
    ToolPolicyConfig,
    resolve_tool_policy,
)

# ==================== 工具沙箱策略 ====================


class TestToolPolicyConfig:
    def test_defaults(self):
        config = ToolPolicyConfig()
        assert config.allow == []
        assert config.also_allow == []
        assert config.deny == []

    def test_custom(self):
        config = ToolPolicyConfig(allow=["echo"], deny=["shell"])
        assert config.allow == ["echo"]
        assert config.deny == ["shell"]


class TestToolPolicy:
    def test_no_restrictions(self):
        """无限制时所有工具允许"""
        policy = ToolPolicy()
        assert policy.is_allowed("any_tool") == (True, "")

    def test_deny_blocks(self):
        """deny 列表优先级最高"""
        policy = ToolPolicy(ToolPolicyConfig(deny=["shell", "filesystem"]))
        allowed, reason = policy.is_allowed("shell")
        assert allowed is False
        assert "denied by policy" in reason
        assert policy.is_allowed("echo") == (True, "")

    def test_allow_restricts(self):
        """有 allow 列表时只允许列表中的工具"""
        policy = ToolPolicy(ToolPolicyConfig(allow=["echo", "time"]))
        assert policy.is_allowed("echo") == (True, "")
        assert policy.is_allowed("time") == (True, "")
        allowed, reason = policy.is_allowed("shell")
        assert allowed is False
        assert "not in allow list" in reason

    def test_also_allow_extends(self):
        """also_allow 在 allow 基础上追加"""
        policy = ToolPolicy(ToolPolicyConfig(allow=["echo"], also_allow=["web_search"]))
        assert policy.is_allowed("echo") == (True, "")
        assert policy.is_allowed("web_search") == (True, "")
        allowed, reason = policy.is_allowed("shell")
        assert allowed is False
        assert "not in allow list" in reason

    def test_also_allow_without_allow(self):
        """无 allow 只有 also_allow 时，所有工具 + also_allow 均可"""
        policy = ToolPolicy(ToolPolicyConfig(also_allow=["extra"]))
        assert policy.is_allowed("echo") == (True, "")
        assert policy.is_allowed("extra") == (True, "")
        assert policy.is_allowed("shell") == (True, "")

    def test_deny_overrides_allow(self):
        """deny 优先级高于 allow"""
        policy = ToolPolicy(ToolPolicyConfig(allow=["echo", "shell"], deny=["shell"]))
        assert policy.is_allowed("echo") == (True, "")
        allowed, reason = policy.is_allowed("shell")
        assert allowed is False
        assert "denied by policy" in reason

    def test_get_allowed_tools(self):
        policy = ToolPolicy(ToolPolicyConfig(allow=["echo", "time"], deny=["shell"]))
        all_tools = ["echo", "time", "shell", "filesystem"]
        allowed = policy.get_allowed_tools(all_tools)
        assert "echo" in allowed
        assert "time" in allowed
        assert "shell" not in allowed
        assert "filesystem" not in allowed

    def test_get_denied_tools(self):
        policy = ToolPolicy(ToolPolicyConfig(deny=["shell"]))
        all_tools = ["echo", "shell"]
        denied = policy.get_denied_tools(all_tools)
        assert denied == ["shell"]

    def test_to_dict(self):
        policy = ToolPolicy(ToolPolicyConfig(allow=["a"], deny=["b"]))
        d = policy.to_dict()
        assert d["allow"] == ["a"]
        assert d["deny"] == ["b"]


class TestResolveToolPolicy:
    def test_none_config(self):
        """无配置返回 None（无限制）"""
        assert resolve_tool_policy(None) is None

    def test_empty_config(self):
        """config 中无 tool_policy 键返回 None"""
        assert resolve_tool_policy({}) is None

    def test_valid_config(self):
        """有效配置返回 ToolPolicy"""
        config = {"tool_policy": {"deny": ["shell"]}}
        policy = resolve_tool_policy(config)
        assert policy is not None
        allowed, reason = policy.is_allowed("shell")
        assert allowed is False
        assert "denied" in reason

    def test_invalid_config(self):
        """无效配置返回 None"""
        config = {"tool_policy": "not a dict"}
        policy = resolve_tool_policy(config)
        assert policy is None


# ==================== 审计日志 ====================


class TestAuditLogger:
    def test_log_basic(self):
        """基本日志记录"""
        with tempfile.TemporaryDirectory() as tmpdir:
            audit = AuditLogger(log_dir=tmpdir)
            audit.log(action="create", actor="user-1", resource="agent:abc")

            # 读取日志文件
            log_file = audit._current_file()
            assert log_file.exists()

            with open(log_file, encoding="utf-8") as f:
                lines = f.readlines()
            assert len(lines) == 1

            entry = json.loads(lines[0])
            assert entry["action"] == "create"
            assert entry["actor"] == "user-1"
            assert entry["resource"] == "agent:abc"
            assert "timestamp" in entry

    def test_log_with_details(self):
        """带详情的日志"""
        with tempfile.TemporaryDirectory() as tmpdir:
            audit = AuditLogger(log_dir=tmpdir)
            audit.log(action="update", actor="system", details={"key": "value"})

            entries = audit.read_logs()
            assert len(entries) == 1
            assert entries[0]["details"]["key"] == "value"

    def test_secret_redaction_full(self):
        """密钥字段完全脱敏"""
        with tempfile.TemporaryDirectory() as tmpdir:
            audit = AuditLogger(log_dir=tmpdir)
            audit.log(
                action="config_change",
                actor="admin",
                sensitive_args={"api_key": "sk-1234567890", "password": "mysecretpass"},
            )

            entries = audit.read_logs()
            assert entries[0]["details"]["api_key"] == "[REDACTED]"
            assert entries[0]["details"]["password"] == "[REDACTED]"

    def test_secret_redaction_partial(self):
        """长值部分脱敏"""
        with tempfile.TemporaryDirectory() as tmpdir:
            audit = AuditLogger(log_dir=tmpdir)
            audit.log(
                action="config_change",
                actor="admin",
                sensitive_args={
                    "display_name": "this is a long value that should be partially redacted"
                },
            )

            entries = audit.read_logs()
            # 非密钥字段，但值>8字符，部分脱敏
            assert entries[0]["details"]["display_name"].endswith("****")

    def test_secret_redaction_short_value(self):
        """短值不脱敏"""
        with tempfile.TemporaryDirectory() as tmpdir:
            audit = AuditLogger(log_dir=tmpdir)
            audit.log(
                action="config_change",
                actor="admin",
                sensitive_args={"status": "ok"},
            )

            entries = audit.read_logs()
            assert entries[0]["details"]["status"] == "ok"

    def test_date_rolling(self):
        """日期滚动文件"""
        with tempfile.TemporaryDirectory() as tmpdir:
            audit = AuditLogger(log_dir=tmpdir)
            audit.log(action="test", actor="system")

            log_file = audit._current_file()
            assert "audit-" in str(log_file)
            assert ".jsonl" in str(log_file)

    def test_read_logs_with_action_filter(self):
        """按 action 过滤"""
        with tempfile.TemporaryDirectory() as tmpdir:
            audit = AuditLogger(log_dir=tmpdir)
            audit.log(action="login", actor="user-1")
            audit.log(action="create", actor="user-1")
            audit.log(action="login", actor="user-2")

            entries = audit.read_logs(action="login")
            assert len(entries) == 2
            assert all(e["action"] == "login" for e in entries)

    def test_read_logs_limit(self):
        """限制返回条数"""
        with tempfile.TemporaryDirectory() as tmpdir:
            audit = AuditLogger(log_dir=tmpdir)
            for i in range(10):
                audit.log(action="test", actor=f"user-{i}")

            entries = audit.read_logs(limit=5)
            assert len(entries) == 5

    def test_read_logs_nonexistent_date(self):
        """不存在的日期返回空"""
        with tempfile.TemporaryDirectory() as tmpdir:
            audit = AuditLogger(log_dir=tmpdir)
            entries = audit.read_logs(date="19990101")
            assert entries == []

    def test_log_login(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            audit = AuditLogger(log_dir=tmpdir)
            audit.log_login("user-1", success=True, ip="127.0.0.1")
            audit.log_login("user-2", success=False, ip="10.0.0.1")

            entries = audit.read_logs()
            assert entries[0]["action"] == "login"
            assert entries[0]["details"]["success"] is True
            assert entries[1]["action"] == "login_failed"
            assert entries[1]["details"]["success"] is False

    def test_log_config_change(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            audit = AuditLogger(log_dir=tmpdir)
            audit.log_config_change(
                "admin",
                "api_key",
                old_value="sk-old-key-12345678",
                new_value="sk-new-key-12345678",
            )

            entries = audit.read_logs()
            assert entries[0]["action"] == "config_change"
            assert entries[0]["resource"] == "config:api_key"
            # api_key 在 sensitive_args 中，作为 key 传入 _redact_secrets
            # "old_value" 不是密钥 key，但值>8字符会部分脱敏
            assert (
                "****" in entries[0]["details"]["old_value"]
                or "[REDACTED]" in entries[0]["details"]["old_value"]
            )

    def test_log_agent_action(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            audit = AuditLogger(log_dir=tmpdir)
            audit.log_agent_action("create", "admin", "agent-123")

            entries = audit.read_logs()
            assert entries[0]["action"] == "agent_create"
            assert entries[0]["resource"] == "agent:agent-123"

    def test_log_tool_execute(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            audit = AuditLogger(log_dir=tmpdir)
            audit.log_tool_execute("system", "shell", allowed=False, agent_id="a1")

            entries = audit.read_logs()
            assert entries[0]["action"] == "tool_blocked"
            assert entries[0]["resource"] == "tool:shell"

    def test_write_failure_safe(self):
        """写入失败不中断"""
        audit = AuditLogger(log_dir="/nonexistent/path/that/cannot/be/created")
        # 不应抛出异常
        audit.log(action="test", actor="system")


class TestSecretKeyPatterns:
    def test_matches_password(self):
        assert SECRET_KEY_PATTERNS.search("password")

    def test_matches_api_key(self):
        assert SECRET_KEY_PATTERNS.search("api_key")

    def test_matches_token(self):
        assert SECRET_KEY_PATTERNS.search("token")

    def test_matches_secret(self):
        assert SECRET_KEY_PATTERNS.search("secret")

    def test_no_match_name(self):
        assert not SECRET_KEY_PATTERNS.search("display_name")

    def test_no_match_status(self):
        assert not SECRET_KEY_PATTERNS.search("status")
