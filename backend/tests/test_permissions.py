"""
权限系统单元测试
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import (
    check_permission,
    get_user_permission_codes,
    has_all_permissions,
    has_any_permission,
)
from app.core.security import get_password_hash
from app.models import Role, User, UserRole
from app.modules.tools.permissions import match_rule, resolve_permission
from app.modules.tools.types import (
    PermissionBehavior,
    PermissionRequest,
    PermissionResult,
    PermissionRule,
    ToolContext,
)

# ==============================================================================
# Legacy app.core.permissions tests（保留对原有权限函数的覆盖）
# ==============================================================================

class FakeTool:
    id = "read_file"
    parameters = {}

    def check_permissions(self, input, ctx):
        return PermissionResult(behavior="ask")


class FakeToolAllow:
    id = "write_file"

    def check_permissions(self, input, ctx):
        return PermissionResult(behavior="allow", reason="tool_allow")


@pytest.fixture
def ctx():
    return ToolContext(
        session_id="s1",
        message_id="m1",
        working_dir="/tmp",
    )


# ------------------------------------------------------------------
# match_rule
# ------------------------------------------------------------------


class TestMatchRule:
    def test_exact_tool_match_no_pattern(self):
        rules = [PermissionRule(tool="read_file", behavior=PermissionBehavior.ALLOW)]
        assert match_rule("read_file", {}, rules) is not None
        assert match_rule("write_file", {}, rules) is None

    def test_wildcard_tool(self):
        rules = [PermissionRule(tool="*", behavior=PermissionBehavior.DENY)]
        assert match_rule("anything", {}, rules) is not None

    def test_pattern_exact_match(self):
        rules = [
            PermissionRule(
                tool="read_file",
                pattern="/etc/passwd",
                behavior=PermissionBehavior.DENY,
            )
        ]
        assert match_rule("read_file", {"file_path": "/etc/passwd"}, rules) is not None
        assert match_rule("read_file", {"file_path": "/tmp/test"}, rules) is None

    def test_pattern_prefix_match(self):
        rules = [
            PermissionRule(
                tool="exec", pattern="rm *", behavior=PermissionBehavior.DENY
            )
        ]
        assert match_rule("exec", {"command": "rm -rf /"}, rules) is not None
        assert match_rule("exec", {"command": "ls -la"}, rules) is None

    def test_no_match_when_tool_differs(self):
        rules = [
            PermissionRule(
                tool="read_file", pattern="/tmp/*", behavior=PermissionBehavior.ALLOW
            )
        ]
        assert match_rule("write_file", {"file_path": "/tmp/test"}, rules) is None


# ------------------------------------------------------------------
# resolve_permission
# ------------------------------------------------------------------


class TestResolvePermission:
    @pytest.mark.asyncio
    async def test_layer1_deny(self, ctx):
        rules = [PermissionRule(tool="read_file", behavior=PermissionBehavior.DENY)]
        result = await resolve_permission(FakeTool(), {}, ctx, rules, "default")
        assert result.behavior == PermissionBehavior.DENY
        assert "rule" in result.reason

    @pytest.mark.asyncio
    async def test_layer1_allow(self, ctx):
        rules = [PermissionRule(tool="read_file", behavior=PermissionBehavior.ALLOW)]
        result = await resolve_permission(FakeTool(), {}, ctx, rules, "default")
        assert result.behavior == PermissionBehavior.ALLOW
        assert "rule" in result.reason

    @pytest.mark.asyncio
    async def test_layer2_yolo(self, ctx):
        result = await resolve_permission(FakeTool(), {}, ctx, [], "yolo")
        assert result.behavior == PermissionBehavior.ALLOW
        assert result.reason == "yolo_mode"

    @pytest.mark.asyncio
    async def test_layer2_plan(self, ctx):
        result = await resolve_permission(FakeTool(), {}, ctx, [], "plan")
        assert result.behavior == PermissionBehavior.DENY
        assert result.reason == "plan_mode"

    @pytest.mark.asyncio
    async def test_layer3_tool_allow(self, ctx):
        result = await resolve_permission(FakeToolAllow(), {}, ctx, [], "default")
        assert result.behavior == PermissionBehavior.ALLOW
        assert result.reason == "tool_allow"

    @pytest.mark.asyncio
    async def test_layer4_ask_callback(self, ctx):
        async def ask_cb(req: PermissionRequest):
            return PermissionResult(
                behavior=PermissionBehavior.ALLOW, reason="user_allowed"
            )

        ctx.ask_callback = ask_cb
        result = await resolve_permission(FakeTool(), {}, ctx, [], "default")
        assert result.behavior == PermissionBehavior.ALLOW
        assert result.reason == "user_allowed"

    @pytest.mark.asyncio
    async def test_layer4_no_callback_defaults_deny(self, ctx):
        result = await resolve_permission(FakeTool(), {}, ctx, [], "default")
        assert result.behavior == PermissionBehavior.DENY
        assert result.reason == "default_deny"


# ==============================================================================
# Legacy app.core.permissions tests（保留对原有权限函数的覆盖）
# ==============================================================================

# ────────────────────────────────────────────
# check_permission — 通配符匹配
# ────────────────────────────────────────────


class TestCheckPermission:
    def test_global_wildcard_matches_everything(self):
        assert check_permission("task:create", ["*"]) is True
        assert check_permission("anything:whatever", ["*"]) is True

    def test_exact_match(self):
        assert check_permission("task:create", ["task:create", "task:read"]) is True
        assert check_permission("task:delete", ["task:create", "task:read"]) is False

    def test_resource_wildcard(self):
        # "task:*" 匹配 "task:create"
        assert check_permission("task:create", ["task:*"]) is True
        assert check_permission("task:read", ["task:*"]) is True
        assert check_permission("task:delete", ["task:*"]) is True
        # "task:*" 不匹配 "agent:create"
        assert check_permission("agent:create", ["task:*"]) is False

    def test_no_match(self):
        assert check_permission("task:create", ["agent:create"]) is False

    def test_empty_user_permissions(self):
        assert check_permission("task:create", []) is False

    def test_multiple_wildcards(self):
        perms = ["task:*", "agent:read", "system:*"]
        assert check_permission("task:delete", perms) is True
        assert check_permission("agent:read", perms) is True
        assert check_permission("agent:create", perms) is False
        assert check_permission("system:config", perms) is True

    def test_nested_colon_not_matched(self):
        # "task:*" 匹配 "task:sub:action" 因为 split(":")[:2] = "task:*"
        # 实际实现中 split(":") parts[0] + ":*" = "task:*" 在列表中
        assert check_permission("task:sub:action", ["task:*"]) is True


# ────────────────────────────────────────────
# has_any_permission / has_all_permissions
# ────────────────────────────────────────────


class TestHasAnyPermission:
    def test_any_match(self):
        # user has task:* wildcard, needs task:create or agent:read
        assert has_any_permission(["task:*"], ["task:create", "agent:read"]) is True

    def test_any_exact_match(self):
        # user has task:create, needs task:create or agent:read
        assert (
            has_any_permission(["task:create"], ["task:create", "agent:read"]) is True
        )

    def test_any_no_match(self):
        assert (
            has_any_permission(["task:create"], ["agent:read", "system:config"])
            is False
        )

    def test_any_empty(self):
        assert has_any_permission([], ["task:*"]) is False
        assert has_any_permission(["task:create"], []) is False


class TestHasAllPermissions:
    def test_all_match(self):
        # user has task:*, needs both task:create and task:read
        assert has_all_permissions(["task:*"], ["task:create", "task:read"]) is True

    def test_partial_match(self):
        # user has task:*, needs task:create AND agent:read
        assert has_all_permissions(["task:*"], ["task:create", "agent:read"]) is False

    def test_all_empty_required(self):
        # all([]) is True — no required permissions means all satisfied
        assert has_all_permissions(["task:*"], []) is True

    def test_all_with_global_wildcard(self):
        # user has global *, needs everything
        assert (
            has_all_permissions(["*"], ["task:create", "agent:read", "system:config"])
            is True
        )


# ────────────────────────────────────────────
# get_user_permission_codes
# ────────────────────────────────────────────


class TestGetUserPermissionCodes:
    @pytest.mark.asyncio
    async def test_super_admin_gets_wildcard(
        self, db_session: AsyncSession, test_admin: User
    ):
        codes = await get_user_permission_codes(test_admin, db_session)
        assert codes == ["*"]

    @pytest.mark.asyncio
    async def test_org_admin_gets_extra_perms(
        self, db_session: AsyncSession, test_org_admin: User, test_role: Role
    ):
        # org_admin 角色码需要匹配 Role 表
        # 先创建 org_admin 角色
        org_admin_role = Role(
            name="组织管理员",
            code="org_admin",
            type="system",
            is_system=True,
            permissions={"codes": ["task:*", "agent:read"]},
        )
        db_session.add(org_admin_role)
        await db_session.commit()

        codes = await get_user_permission_codes(test_org_admin, db_session)
        # 应包含角色权限 + 组织管理员额外权限
        assert "org:*" in codes
        assert "user:*" in codes
        assert "role:read" in codes

    @pytest.mark.asyncio
    async def test_normal_user_gets_role_perms(
        self, db_session: AsyncSession, test_user: User, test_role: Role
    ):
        # test_user 的 role 是 UserRole.USER, value = "user"
        # test_role 的 code = "user"
        codes = await get_user_permission_codes(test_user, db_session)
        assert "task:read" in codes
        assert "task:create" in codes
        assert "agent:read" in codes

    @pytest.mark.asyncio
    async def test_user_with_no_role(self, db_session: AsyncSession, test_org):
        """用户角色在 Role 表中不存在时仍返回基础权限"""
        user = User(
            username="noroleuser",
            email="norole@example.com",
            display_name="无角色用户",
            hashed_password=get_password_hash("pass123456"),
            role=UserRole.USER,
            is_active=True,
            organization_id=test_org.id,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        # 删除 test_role 避免冲突
        from sqlalchemy import select

        result = await db_session.execute(select(Role).where(Role.code == "user"))
        role = result.scalar_one_or_none()
        if role:
            await db_session.delete(role)
            await db_session.commit()

        codes = await get_user_permission_codes(user, db_session)
        # 没有角色时仍应有基本权限
        assert "dashboard:view" in codes
        assert "chat:view" in codes
        assert "chat:create" in codes


# ────────────────────────────────────────────
# PermissionChecker 依赖注入行为
# ────────────────────────────────────────────


class TestPermissionCheckerBehavior:
    """测试 PermissionChecker 的逻辑，不通过 HTTP 请求"""

    @pytest.mark.asyncio
    async def test_super_admin_bypasses_check(
        self, db_session: AsyncSession, test_admin: User
    ):
        from app.core.permissions import PermissionChecker

        checker = PermissionChecker("task:create")
        # 超级管理员直接通过
        result = await checker(current_user=test_admin, db=db_session)
        assert result == test_admin

    @pytest.mark.asyncio
    async def test_user_with_permission_passes(
        self, db_session: AsyncSession, test_user: User, test_role: Role
    ):
        from app.core.permissions import PermissionChecker

        checker = PermissionChecker("task:read")
        result = await checker(current_user=test_user, db=db_session)
        assert result == test_user

    @pytest.mark.asyncio
    async def test_user_without_permission_fails(
        self, db_session: AsyncSession, test_user: User, test_role: Role
    ):
        from app.core.permissions import PermissionChecker

        checker = PermissionChecker("task:delete")
        with pytest.raises(HTTPException) as exc_info:
            await checker(current_user=test_user, db=db_session)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_any_match_semantics(
        self, db_session: AsyncSession, test_user: User, test_role: Role
    ):
        from app.core.permissions import PermissionChecker

        # test_role 有 task:read 但没有 task:delete
        checker = PermissionChecker(["task:delete", "task:read"])
        result = await checker(current_user=test_user, db=db_session)
        assert result == test_user  # 任一匹配即通过

    @pytest.mark.asyncio
    async def test_single_string_permission(
        self, db_session: AsyncSession, test_admin: User
    ):
        from app.core.permissions import PermissionChecker

        checker = PermissionChecker("system:config")
        result = await checker(current_user=test_admin, db=db_session)
        assert result == test_admin


class TestPermissionCheckerAllBehavior:
    @pytest.mark.asyncio
    async def test_super_admin_bypasses_all_check(
        self, db_session: AsyncSession, test_admin: User
    ):
        from app.core.permissions import PermissionCheckerAll

        checker = PermissionCheckerAll(["task:create", "task:delete", "system:config"])
        result = await checker(current_user=test_admin, db=db_session)
        assert result == test_admin

    @pytest.mark.asyncio
    async def test_user_with_all_permissions_passes(
        self, db_session: AsyncSession, test_user: User, test_role: Role
    ):
        from app.core.permissions import PermissionCheckerAll

        checker = PermissionCheckerAll(["task:read", "task:create"])
        result = await checker(current_user=test_user, db=db_session)
        assert result == test_user

    @pytest.mark.asyncio
    async def test_user_missing_one_permission_fails(
        self, db_session: AsyncSession, test_user: User, test_role: Role
    ):
        from app.core.permissions import PermissionCheckerAll

        # test_role 有 task:read, task:create 但没有 task:delete
        checker = PermissionCheckerAll(["task:read", "task:delete"])
        with pytest.raises(HTTPException) as exc_info:
            await checker(current_user=test_user, db=db_session)
        assert exc_info.value.status_code == 403
        assert "task:delete" in exc_info.value.detail
