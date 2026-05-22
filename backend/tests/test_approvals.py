"""
审批流程 API 测试

测试审批的完整流程：提交 -> 审批 -> 生效
"""

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token, get_password_hash
from app.models import Skill, User


@pytest_asyncio.fixture
async def test_user_approval(db_session: AsyncSession, test_org) -> User:
    """创建测试用户（用于审批测试）"""
    from app.models import UserRole

    user = User(
        username="approval_user",
        email="approval_user@example.com",
        display_name="Approval User",
        hashed_password=get_password_hash("password"),
        role=UserRole.USER,
        is_active=True,
        organization_id=test_org.id,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def test_skill(db_session: AsyncSession, test_user_approval: User) -> Skill:
    """创建测试技能"""
    skill = Skill(
        name="test-skill-approval",
        display_name="Test Skill for Approval",
        description="A test skill for approval testing",
        content="test content",
        creator_id=test_user_approval.id,
        scope="user",
        is_active=True,
    )
    db_session.add(skill)
    await db_session.commit()
    await db_session.refresh(skill)
    return skill


@pytest.fixture
def auth_headers_approval(test_user_approval: User) -> dict:
    """生成认证请求头"""
    token = create_access_token(data={"sub": str(test_user_approval.id)})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def org_admin_headers(test_org_admin: User) -> dict:
    """生成组织管理员认证请求头"""
    token = create_access_token(data={"sub": str(test_org_admin.id)})
    return {"Authorization": f"Bearer {token}"}


class TestCreateApproval:
    """提交审批测试"""

    @pytest.mark.asyncio
    async def test_create_approval_skill(
        self, client: AsyncClient, test_skill: Skill, auth_headers_approval: dict
    ):
        """提交 Skill 共享审批"""
        response = await client.post(
            "/api/approvals",
            json={
                "approval_type": "skill_to_org",
                "title": "共享技能到组织",
                "description": "申请将此技能共享到组织",
                "resource_type": "skill",
                "resource_id": str(test_skill.id),
                "target_scope": "org",
            },
            headers=auth_headers_approval,
        )
        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "pending"
        assert data["resource_type"] == "skill"
        assert data["target_scope"] == "org"

    @pytest.mark.asyncio
    async def test_create_approval_duplicate(
        self, client: AsyncClient, test_skill: Skill, auth_headers_approval: dict
    ):
        """重复提交相同审批"""
        # 第一次提交
        await client.post(
            "/api/approvals",
            json={
                "approval_type": "skill_to_org",
                "title": "共享技能",
                "resource_type": "skill",
                "resource_id": str(test_skill.id),
                "target_scope": "org",
            },
            headers=auth_headers_approval,
        )

        # 第二次提交
        response = await client.post(
            "/api/approvals",
            json={
                "approval_type": "skill_to_org",
                "title": "共享技能",
                "resource_type": "skill",
                "resource_id": str(test_skill.id),
                "target_scope": "org",
            },
            headers=auth_headers_approval,
        )
        assert response.status_code == 400
        assert "已有待审批" in response.json()["detail"]


class TestListApprovals:
    """审批列表测试"""

    @pytest.mark.asyncio
    async def test_list_approvals_as_user(
        self,
        client: AsyncClient,
        test_user_approval: User,
        test_skill: Skill,
        auth_headers_approval: dict,
    ):
        """普通用户查看审批列表"""
        # 提交一个审批
        await client.post(
            "/api/approvals",
            json={
                "approval_type": "skill_to_org",
                "title": "共享技能",
                "resource_type": "skill",
                "resource_id": str(test_skill.id),
                "target_scope": "org",
            },
            headers=auth_headers_approval,
        )

        response = await client.get("/api/approvals", headers=auth_headers_approval)
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 1
        # 用户只能看到自己提交的
        for approval in data:
            assert approval["requester_id"] == test_user_approval.id

    @pytest.mark.asyncio
    async def test_get_pending_count(
        self, client: AsyncClient, test_skill: Skill, auth_headers_approval: dict
    ):
        """获取待审批数量"""
        # 提交一个审批
        await client.post(
            "/api/approvals",
            json={
                "approval_type": "skill_to_org",
                "title": "共享技能",
                "resource_type": "skill",
                "resource_id": str(test_skill.id),
                "target_scope": "org",
            },
            headers=auth_headers_approval,
        )

        response = await client.get(
            "/api/approvals/pending-count", headers=auth_headers_approval
        )
        assert response.status_code == 200
        data = response.json()
        assert "pending_count" in data
        assert data["pending_count"] >= 1


class TestReviewApproval:
    """审批操作测试"""

    @pytest.mark.asyncio
    async def test_review_approve(
        self,
        client: AsyncClient,
        test_skill: Skill,
        test_org_admin: User,
        test_org,
        db_session: AsyncSession,
        auth_headers_approval: dict,
        org_admin_headers: dict,
    ):
        """批准审批"""
        create_resp = await client.post(
            "/api/approvals",
            json={
                "approval_type": "skill_to_org",
                "title": "共享技能",
                "resource_type": "skill",
                "resource_id": str(test_skill.id),
                "target_scope": "org",
                "target_org_id": test_org.id,
            },
            headers=auth_headers_approval,
        )
        approval_id = create_resp.json()["id"]

        # 组织管理员批准
        response = await client.post(
            f"/api/approvals/{approval_id}/review",
            json={"approved": True, "review_comment": "批准"},
            headers=org_admin_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "approved"

        # 验证 skill scope 变更
        await db_session.refresh(test_skill)
        assert test_skill.scope == "org"

    @pytest.mark.asyncio
    async def test_review_reject(
        self,
        client: AsyncClient,
        test_skill: Skill,
        test_org,
        auth_headers_approval: dict,
        org_admin_headers: dict,
    ):
        """拒绝审批"""
        create_resp = await client.post(
            "/api/approvals",
            json={
                "approval_type": "skill_to_org",
                "title": "共享技能",
                "resource_type": "skill",
                "resource_id": str(test_skill.id),
                "target_scope": "org",
                "target_org_id": test_org.id,
            },
            headers=auth_headers_approval,
        )
        approval_id = create_resp.json()["id"]

        # 组织管理员拒绝
        response = await client.post(
            f"/api/approvals/{approval_id}/review",
            json={"approved": False, "review_comment": "不符合要求"},
            headers=org_admin_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "rejected"


class TestCancelApproval:
    """取消审批测试"""

    @pytest.mark.asyncio
    async def test_cancel_approval(
        self, client: AsyncClient, test_skill: Skill, auth_headers_approval: dict
    ):
        """取消审批"""
        # 提交审批
        create_resp = await client.post(
            "/api/approvals",
            json={
                "approval_type": "skill_to_org",
                "title": "共享技能",
                "resource_type": "skill",
                "resource_id": str(test_skill.id),
                "target_scope": "org",
            },
            headers=auth_headers_approval,
        )
        approval_id = create_resp.json()["id"]

        # 取消审批
        response = await client.post(
            f"/api/approvals/{approval_id}/cancel",
            headers=auth_headers_approval,
        )
        assert response.status_code == 200


class TestGetApproval:
    """获取审批详情测试"""

    @pytest.mark.asyncio
    async def test_get_approval_detail(
        self, client: AsyncClient, test_skill: Skill, auth_headers_approval: dict
    ):
        """获取审批详情"""
        # 提交审批
        create_resp = await client.post(
            "/api/approvals",
            json={
                "approval_type": "skill_to_org",
                "title": "共享技能",
                "description": "测试描述",
                "resource_type": "skill",
                "resource_id": str(test_skill.id),
                "target_scope": "org",
            },
            headers=auth_headers_approval,
        )
        approval_id = create_resp.json()["id"]

        # 获取详情
        response = await client.get(
            f"/api/approvals/{approval_id}", headers=auth_headers_approval
        )
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == approval_id
        assert data["title"] == "共享技能"
        assert data["description"] == "测试描述"
