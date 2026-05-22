"""
Permission Mode 测试

测试权限模式层级、角色映射、权限检查器、工具门控。
"""

from app.core.permission_mode import (
    PermissionChecker,
    PermissionCheckResult,
    PermissionMode,
    get_default_mode_for_role,
    get_max_mode_for_role,
    resolve_permission_mode,
)
from app.core.sandbox_policy import ToolPolicy, ToolPolicyConfig

# ==================== PermissionMode ====================


class TestPermissionMode:
    """权限模式枚举"""

    def test_five_modes(self):
        assert len(PermissionMode) == 5
        modes = list(PermissionMode)
        assert PermissionMode.READ_ONLY in modes
        assert PermissionMode.WORKSPACE_WRITE in modes
        assert PermissionMode.DANGER_FULL_ACCESS in modes
        assert PermissionMode.PROMPT in modes
        assert PermissionMode.ALLOW in modes

    def test_mode_order(self):
        """模式强度排序"""
        assert PermissionMode.READ_ONLY < PermissionMode.WORKSPACE_WRITE
        assert PermissionMode.WORKSPACE_WRITE < PermissionMode.DANGER_FULL_ACCESS
        assert PermissionMode.DANGER_FULL_ACCESS < PermissionMode.PROMPT
        assert PermissionMode.PROMPT < PermissionMode.ALLOW

    def test_at_least(self):
        assert PermissionMode.WORKSPACE_WRITE.at_least(PermissionMode.READ_ONLY) is True
        assert (
            PermissionMode.WORKSPACE_WRITE.at_least(PermissionMode.WORKSPACE_WRITE)
            is True
        )
        assert (
            PermissionMode.WORKSPACE_WRITE.at_least(PermissionMode.DANGER_FULL_ACCESS)
            is False
        )

    def test_allow_is_highest(self):
        assert PermissionMode.ALLOW.at_least(PermissionMode.READ_ONLY)
        assert PermissionMode.ALLOW.at_least(PermissionMode.WORKSPACE_WRITE)
        assert PermissionMode.ALLOW.at_least(PermissionMode.DANGER_FULL_ACCESS)
        assert PermissionMode.ALLOW.at_least(PermissionMode.PROMPT)

    def test_string_values(self):
        assert PermissionMode.READ_ONLY.value == "read_only"
        assert PermissionMode.WORKSPACE_WRITE.value == "workspace_write"
        assert PermissionMode.DANGER_FULL_ACCESS.value == "danger_full_access"
        assert PermissionMode.PROMPT.value == "prompt"
        assert PermissionMode.ALLOW.value == "allow"


# ==================== Role → Mode Mapping ====================


class TestRoleModeMapping:
    """角色 → 权限模式映射"""

    def test_super_admin_max_mode(self):
        assert get_max_mode_for_role("super_admin") == PermissionMode.ALLOW

    def test_org_admin_max_mode(self):
        assert get_max_mode_for_role("org_admin") == PermissionMode.DANGER_FULL_ACCESS

    def test_user_max_mode(self):
        assert get_max_mode_for_role("user") == PermissionMode.WORKSPACE_WRITE

    def test_unknown_role_fallback(self):
        """未知角色 fallback 为 workspace_write"""
        assert get_max_mode_for_role("unknown") == PermissionMode.WORKSPACE_WRITE

    def test_default_modes_match(self):
        """角色默认模式应匹配最大模式"""
        for role in ("super_admin", "org_admin", "user"):
            assert get_default_mode_for_role(role) == get_max_mode_for_role(role)

    def test_enum_input(self):
        """接受 UserRole 枚举"""
        import enum

        class MockRole(str, enum.Enum):
            SUPER_ADMIN = "super_admin"
            USER = "user"

        assert get_max_mode_for_role(MockRole.SUPER_ADMIN) == PermissionMode.ALLOW
        assert get_max_mode_for_role(MockRole.USER) == PermissionMode.WORKSPACE_WRITE


# ==================== resolve_permission_mode ====================


class TestResolvePermissionMode:
    """模式解析"""

    def test_user_default(self):
        """无配置时按角色取默认"""
        mode = resolve_permission_mode(user_role="user")
        assert mode == PermissionMode.WORKSPACE_WRITE

    def test_super_admin_default(self):
        mode = resolve_permission_mode(user_role="super_admin")
        assert mode == PermissionMode.ALLOW

    def test_agent_config_overrides(self):
        """Agent.config 覆盖角色默认（降级）"""
        mode = resolve_permission_mode(
            user_role="super_admin",
            agent_config={"permission_mode": "read_only"},
        )
        assert mode == PermissionMode.READ_ONLY

    def test_user_cannot_elevate(self):
        """普通用户不能越权"""
        mode = resolve_permission_mode(
            user_role="user",
            agent_config={"permission_mode": "allow"},
        )
        assert mode == PermissionMode.WORKSPACE_WRITE  # 被角色上限裁剪

    def test_org_admin_cannot_set_allow(self):
        mode = resolve_permission_mode(
            user_role="org_admin",
            agent_config={"permission_mode": "allow"},
        )
        assert mode == PermissionMode.DANGER_FULL_ACCESS

    def test_db_settings_fallback(self):
        """无 Agent 配置时用系统设置"""
        mode = resolve_permission_mode(
            user_role="user",
            db_settings={"permission_mode": "danger_full_access"},
        )
        # USER 角色上限是 workspace_write，系统设置 danger_full_access 会被裁剪
        assert mode == PermissionMode.WORKSPACE_WRITE

    def test_db_settings_within_ceiling(self):
        """系统设置在角色上限内则直接采用"""
        mode = resolve_permission_mode(
            user_role="user",
            db_settings={"permission_mode": "read_only"},
        )
        assert mode == PermissionMode.READ_ONLY

    def test_db_settings_also_clamped(self):
        """系统设置也被角色上限裁剪"""
        mode = resolve_permission_mode(
            user_role="user",
            db_settings={"permission_mode": "allow"},
        )
        assert mode == PermissionMode.WORKSPACE_WRITE

    def test_invalid_agent_config_fallback(self):
        """无效配置值 fallback 到默认"""
        mode = resolve_permission_mode(
            user_role="user",
            agent_config={"permission_mode": "nonexistent_mode"},
        )
        assert mode == PermissionMode.WORKSPACE_WRITE

    def test_no_role_no_config(self):
        """无角色无配置 → workspace_write"""
        mode = resolve_permission_mode()
        assert mode == PermissionMode.WORKSPACE_WRITE


# ==================== PermissionCheckResult ====================


class TestPermissionCheckResult:
    """检查结果构造"""

    def test_ok(self):
        r = PermissionCheckResult.ok()
        assert r.allowed is True
        assert r.require_confirmation is False

    def test_denied(self):
        r = PermissionCheckResult.denied("no permission")
        assert r.allowed is False
        assert r.reason == "no permission"

    def test_confirmation_required(self):
        r = PermissionCheckResult.confirmation_required("需要确认")
        assert r.allowed is True
        assert r.require_confirmation is True
        assert r.reason == "需要确认"


# ==================== PermissionChecker ====================


class TestPermissionCheckerCheckTool:
    """工具检查"""

    def test_allow_mode_all_tools(self):
        checker = PermissionChecker(mode=PermissionMode.ALLOW)
        assert checker.check_tool("shell").allowed
        assert checker.check_tool("filesystem").allowed
        assert checker.check_tool("exec").allowed
        assert checker.check_tool("read_file").allowed

    def test_read_only_allows_read(self):
        checker = PermissionChecker(mode=PermissionMode.READ_ONLY)
        assert checker.check_tool("read_file").allowed
        assert checker.check_tool("echo").allowed
        assert checker.check_tool("web_search").allowed

    def test_read_only_denies_write_tools(self):
        checker = PermissionChecker(mode=PermissionMode.READ_ONLY)
        for tool in (
            "exec",
            "shell",
            "filesystem",
            "write_file",
            "delete_file",
            "git",
            "task",
            "skill",
            "sub_agent",
        ):
            result = checker.check_tool(tool)
            assert not result.allowed, (
                f"ReadOnly should deny {tool}, got allowed={result.allowed}"
            )

    def test_workspace_write_allows_most(self):
        checker = PermissionChecker(mode=PermissionMode.WORKSPACE_WRITE)
        assert checker.check_tool("exec").allowed
        assert checker.check_tool("filesystem").allowed
        assert checker.check_tool("read_file").allowed

    def test_tool_policy_deny_overrides(self):
        """ToolPolicy deny 列表优先于 PermissionMode"""
        policy = ToolPolicy(ToolPolicyConfig(deny=["shell", "exec"]))
        checker = PermissionChecker(
            mode=PermissionMode.ALLOW,  # allow 模式也绕不过 deny
            tool_policy=policy,
        )
        assert not checker.check_tool("shell").allowed
        assert not checker.check_tool("exec").allowed
        assert checker.check_tool("read_file").allowed

    def test_tool_policy_allow_list(self):
        """ToolPolicy allow 列表限制可用工具"""
        policy = ToolPolicy(ToolPolicyConfig(allow=["echo", "read_file"]))
        checker = PermissionChecker(
            mode=PermissionMode.DANGER_FULL_ACCESS,
            tool_policy=policy,
        )
        assert checker.check_tool("echo").allowed
        assert checker.check_tool("read_file").allowed
        assert not checker.check_tool("shell").allowed

    def test_command_approval_setting(self):
        """command_approval=true 时 exec 需要确认"""
        checker = PermissionChecker(
            mode=PermissionMode.WORKSPACE_WRITE,
            settings={"command_approval": "true"},
        )
        result = checker.check_tool("exec")
        assert result.allowed
        assert result.require_confirmation

    def test_command_approval_disabled(self):
        """command_approval=false 时 exec 直接放行"""
        checker = PermissionChecker(
            mode=PermissionMode.WORKSPACE_WRITE,
            settings={"command_approval": "false"},
        )
        result = checker.check_tool("exec")
        assert result.allowed
        assert not result.require_confirmation

    def test_file_approval_enabled(self):
        """file_approval=true 时文件工具需要确认"""
        checker = PermissionChecker(
            mode=PermissionMode.WORKSPACE_WRITE,
            settings={"file_approval": "true"},
        )
        for tool in ("write_file", "delete_file", "replace_in_file"):
            result = checker.check_tool(tool)
            assert result.require_confirmation, f"{tool} should require confirmation"


# ==================== Bash Check ====================


class TestPermissionCheckerCheckBash:
    """Bash 命令检查"""

    def test_allow_mode_auto_approves(self):
        checker = PermissionChecker(mode=PermissionMode.ALLOW)
        result = checker.check_bash("rm -rf /")
        assert result.allowed
        assert not result.require_confirmation

    def test_prompt_mode_auto_approves(self):
        checker = PermissionChecker(mode=PermissionMode.PROMPT)
        result = checker.check_bash("rm -rf /")
        assert result.allowed
        assert not result.require_confirmation

    def test_read_only_denies_bash(self):
        checker = PermissionChecker(mode=PermissionMode.READ_ONLY)
        result = checker.check_bash("ls")
        assert not result.allowed

    def test_workspace_write_allows_safe_bash(self):
        checker = PermissionChecker(mode=PermissionMode.WORKSPACE_WRITE)
        result = checker.check_bash("ls")
        assert result.allowed

    def test_danger_level_blocked(self):
        from app.core.bash_safety import DangerLevel

        checker = PermissionChecker(mode=PermissionMode.DANGER_FULL_ACCESS)
        result = checker.check_bash("rm -rf /", danger_level=DangerLevel.BLOCKED)
        assert not result.allowed

    def test_danger_level_dangerous_with_full_access(self):
        from app.core.bash_safety import DangerLevel

        checker = PermissionChecker(
            mode=PermissionMode.DANGER_FULL_ACCESS,
            settings={"command_approval": "true"},
        )
        result = checker.check_bash("rm -rf /", danger_level=DangerLevel.DANGEROUS)
        assert result.require_confirmation

    def test_danger_level_caution(self):
        from app.core.bash_safety import DangerLevel

        checker = PermissionChecker(
            mode=PermissionMode.WORKSPACE_WRITE,
            settings={"command_approval": "true"},
        )
        result = checker.check_bash("git push", danger_level=DangerLevel.CAUTION)
        assert result.require_confirmation


# ==================== File Write Check ====================


class TestPermissionCheckerCheckFileWrite:
    """文件写检查"""

    def test_allow_mode_any_path(self):
        checker = PermissionChecker(mode=PermissionMode.ALLOW)
        assert checker.check_file_write("/etc/passwd").allowed

    def test_read_only_denies(self):
        checker = PermissionChecker(mode=PermissionMode.READ_ONLY)
        assert not checker.check_file_write("/tmp/test.txt").allowed

    def test_danger_full_access_allows(self):
        checker = PermissionChecker(mode=PermissionMode.DANGER_FULL_ACCESS)
        assert checker.check_file_write("/tmp/test.txt").allowed

    def test_workspace_write_within_workspace(self):
        checker = PermissionChecker(
            mode=PermissionMode.WORKSPACE_WRITE,
            workspace_root="/home/user/project",
        )
        result = checker.check_file_write("/home/user/project/src/file.py")
        assert result.allowed
        assert not result.require_confirmation

    def test_workspace_write_outside_workspace_needs_confirmation(self):
        """WorkspaceWrite 模式：workspace 外需要确认，不是硬拒绝"""
        checker = PermissionChecker(
            mode=PermissionMode.WORKSPACE_WRITE,
            workspace_root="/home/user/project",
        )
        result = checker.check_file_write("/etc/passwd")
        assert result.allowed  # 允许但需确认
        assert result.require_confirmation

    def test_workspace_write_no_root(self):
        """无 workspace_root 时直接放行"""
        checker = PermissionChecker(mode=PermissionMode.WORKSPACE_WRITE)
        result = checker.check_file_write("/tmp/test.txt")
        assert result.allowed

    def test_file_approval_setting(self):
        checker = PermissionChecker(
            mode=PermissionMode.WORKSPACE_WRITE,
            workspace_root="/home/user/project",
            settings={"file_approval": "true"},
        )
        result = checker.check_file_write("/home/user/project/src/file.py")
        assert result.require_confirmation  # 需要审批


# ==================== Network Check ====================


class TestPermissionCheckerCheckNetwork:
    """网络请求检查"""

    def test_allow_mode_allows(self):
        checker = PermissionChecker(mode=PermissionMode.ALLOW)
        assert checker.check_network("https://api.example.com").allowed

    def test_read_only_allows_read_only(self):
        """ReadOnly 允许只读网络请求（不硬拒绝，标记确认）"""
        checker = PermissionChecker(mode=PermissionMode.READ_ONLY)
        result = checker.check_network("https://api.example.com")
        assert not result.allowed  # ReadOnly 限制网络

    def test_workspace_write_allows(self):
        checker = PermissionChecker(mode=PermissionMode.WORKSPACE_WRITE)
        assert checker.check_network("https://api.example.com").allowed

    def test_network_approval_setting(self):
        checker = PermissionChecker(
            mode=PermissionMode.WORKSPACE_WRITE,
            settings={"network_approval": "true"},
        )
        result = checker.check_network("https://api.example.com")
        assert result.require_confirmation


# ==================== Role Ceiling ====================


class TestRoleCeiling:
    """角色上限裁剪"""

    def test_user_always_clamped(self):
        """USER 角色的上限永远是 WorkspaceWrite"""
        cases = [
            ("allow", PermissionMode.WORKSPACE_WRITE),
            ("prompt", PermissionMode.WORKSPACE_WRITE),
            ("danger_full_access", PermissionMode.WORKSPACE_WRITE),
            ("workspace_write", PermissionMode.WORKSPACE_WRITE),
            ("read_only", PermissionMode.READ_ONLY),
        ]
        for agent_cfg, expected in cases:
            mode = resolve_permission_mode(
                user_role="user",
                agent_config={"permission_mode": agent_cfg},
            )
            assert mode == expected, (
                f"USER + {agent_cfg} should be {expected}, got {mode}"
            )

    def test_org_admin_clamped(self):
        """ORG_ADMIN 不能设为 Allow"""
        mode = resolve_permission_mode(
            user_role="org_admin",
            agent_config={"permission_mode": "allow"},
        )
        assert mode == PermissionMode.DANGER_FULL_ACCESS

    def test_super_admin_not_clamped(self):
        """SUPER_ADMIN 设 Allow 不会被裁剪"""
        mode = resolve_permission_mode(
            user_role="super_admin",
            agent_config={"permission_mode": "allow"},
        )
        assert mode == PermissionMode.ALLOW

    def test_super_admin_can_downgrade(self):
        mode = resolve_permission_mode(
            user_role="super_admin",
            agent_config={"permission_mode": "read_only"},
        )
        assert mode == PermissionMode.READ_ONLY


# ==================== Integration Tests ====================


class TestIntegration:
    """集成测试"""

    def test_agentloop_accepts_permission_params(self):
        """AgentLoop 接受 permission_mode 和 user_role"""
        from app.modules.agent import AgentLoop

        class MockProvider:
            async def chat_stream(self, *args, **kwargs):
                yield "test"

        loop = AgentLoop(
            provider=MockProvider(),
            permission_mode="read_only",
            user_role="user",
        )
        assert loop._permission_mode == PermissionMode.READ_ONLY
        assert loop._user_role == "user"

    def test_agentloop_default_no_params(self):
        """不传权限参数也能正常初始化"""
        from app.modules.agent import AgentLoop

        class MockProvider:
            async def chat_stream(self, *args, **kwargs):
                yield "test"

        loop = AgentLoop(provider=MockProvider())
        assert loop._permission_mode is None  # 延迟解析
        assert loop._permission_checker is None

    def test_check_tool_policy_integration(self):
        """_check_tool_policy 返回 (allowed, reason)"""
        from app.modules.agent import AgentLoop

        class MockProvider:
            async def chat_stream(self, *args, **kwargs):
                yield "test"

        loop = AgentLoop(
            provider=MockProvider(),
            permission_mode="read_only",
        )
        allowed, reason = loop._check_tool_policy("shell")
        assert not allowed
        assert "ReadOnly" in reason

    def test_check_tool_policy_allows(self):
        """ALLOW 模式下所有工具通过"""
        from app.modules.agent import AgentLoop

        class MockProvider:
            async def chat_stream(self, *args, **kwargs):
                yield "test"

        loop = AgentLoop(
            provider=MockProvider(),
            permission_mode="allow",
        )
        allowed, reason = loop._check_tool_policy("shell")
        assert allowed
        assert reason == ""

    def test_agentloop_with_tool_policy_deny(self):
        """AgentLoop 带 tool_policy deny"""
        from app.modules.agent import AgentLoop

        class MockProvider:
            async def chat_stream(self, *args, **kwargs):
                yield "test"

        loop = AgentLoop(
            provider=MockProvider(),
            permission_mode="allow",
            agent_config={
                "tool_policy": {
                    "deny": ["shell", "exec"],
                }
            },
        )
        allowed, _ = loop._check_tool_policy("shell")
        assert not allowed
        allowed, _ = loop._check_tool_policy("read_file")
        assert allowed

    def test_agentloop_permission_mode_from_config(self):
        """AgentLoop 从 agent_config 解析权限模式（但不传 permission_mode）"""
        from app.modules.agent import AgentLoop

        class MockProvider:
            async def chat_stream(self, *args, **kwargs):
                yield "test"

        loop = AgentLoop(
            provider=MockProvider(),
            user_role="user",
            agent_config={"permission_mode": "read_only"},
        )
        # 延迟初始化，通过 checker 验证
        checker = loop._get_permission_checker()
        assert checker.mode == PermissionMode.READ_ONLY


# ==================== Edge Cases ====================


class TestEdgeCases:
    """边界情况"""

    def test_empty_settings(self):
        checker = PermissionChecker()
        result = checker.check_tool("read_file")
        assert result.allowed

    def test_no_tool_policy(self):
        checker = PermissionChecker(tool_policy=None)
        result = checker.check_tool("shell")
        assert result.allowed

    def test_bash_no_danger_level(self):
        """不传 danger_level 时默认放行"""
        checker = PermissionChecker(mode=PermissionMode.WORKSPACE_WRITE)
        result = checker.check_bash("ls -la")
        assert result.allowed

    def test_check_file_write_empty_path(self):
        checker = PermissionChecker(mode=PermissionMode.WORKSPACE_WRITE)
        result = checker.check_file_write("")
        assert result.allowed

    def test_workspace_root_path_normalization(self):
        """路径规范化处理"""
        checker = PermissionChecker(
            mode=PermissionMode.WORKSPACE_WRITE,
            workspace_root="C:\\Users\\test\\project",
        )
        # Windows 路径
        import os

        if os.name == "nt":
            assert checker.check_file_write(
                "C:\\Users\\test\\project\\file.txt"
            ).allowed
        # POSIX 路径
        result = checker.check_file_write("C:/Users/test/project/file.txt")
        # 至少不崩溃
        assert isinstance(result, PermissionCheckResult)

    def test_permission_checker_initialized_once(self):
        """_get_permission_checker 只初始化一次"""
        from app.modules.agent import AgentLoop

        class MockProvider:
            async def chat_stream(self, *args, **kwargs):
                yield "test"

        loop = AgentLoop(
            provider=MockProvider(),
            permission_mode="workspace_write",
        )
        c1 = loop._get_permission_checker()
        c2 = loop._get_permission_checker()
        assert c1 is c2

    def test_setting_bool_variants(self):
        """设置值不同写法都能正确解析"""
        checker = PermissionChecker(settings={"command_approval": "1"})
        result = checker.check_tool("exec")
        assert result.require_confirmation

        checker2 = PermissionChecker(settings={"command_approval": "yes"})
        assert checker2.check_tool("exec").require_confirmation

        checker3 = PermissionChecker(settings={"command_approval": "0"})
        assert not checker3.check_tool("exec").require_confirmation
