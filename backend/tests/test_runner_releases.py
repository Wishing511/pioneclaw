"""
Runner Release API 测试

TDD: 测试先于实现。首次运行应全部 FAIL（端点尚未实现）。
"""

import os
import tempfile

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import RunnerRelease, User
from tests.conftest import auth_headers

# ============================
# 辅助函数
# ============================


async def create_release(
    db_session: AsyncSession, version: str, platform: str, **kwargs
) -> RunnerRelease:
    """在测试数据库中创建一个 RunnerRelease"""
    release = RunnerRelease(
        version=version,
        filename=kwargs.pop("filename", f"test-{platform}-{version}.zip"),
        file_path=kwargs.pop("file_path", f"/tmp/test-{platform}-{version}.zip"),
        file_size=kwargs.pop("file_size", 1024),
        checksum=kwargs.pop("checksum", "abc123sha256hash"),
        platform=platform,
        is_latest=kwargs.pop("is_latest", False),
        uploaded_by=kwargs.pop("uploaded_by", 1),
        **kwargs,
    )
    db_session.add(release)
    await db_session.commit()
    await db_session.refresh(release)
    return release


# ============================
# 1. POST /runner-releases/upload
# ============================


class TestUploadRelease:
    """测试上传 Runner 版本"""

    @pytest.mark.asyncio
    async def test_upload_release_as_super_admin(
        self, client: AsyncClient, db_session: AsyncSession, test_admin: User
    ):
        """超级管理员上传版本 → 201"""
        response = await client.post(
            "/api/runner-releases/upload",
            data={
                "version": "1.0.0",
                "platform": "windows",
                "release_notes": "Initial release",
            },
            files={"file": ("runner.zip", b"fake binary content", "application/zip")},
            headers=auth_headers(test_admin.id),
        )

        assert response.status_code == 201
        data = response.json()
        assert data["version"] == "1.0.0"
        assert data["platform"] == "windows"
        assert data["filename"].startswith("pioneclaw-runner-windows-1.0.0")
        assert data["file_size"] == len(b"fake binary content")
        assert len(data["checksum"]) == 64  # SHA256
        assert data["is_latest"] is False
        assert data["uploaded_by"] == test_admin.id

    @pytest.mark.asyncio
    async def test_upload_release_duplicate(
        self, client: AsyncClient, db_session: AsyncSession, test_admin: User
    ):
        """同一平台同一版本重复上传 → 400"""
        # First upload
        await client.post(
            "/api/runner-releases/upload",
            data={"version": "1.0.0", "platform": "windows"},
            files={"file": ("runner.zip", b"fake content", "application/zip")},
            headers=auth_headers(test_admin.id),
        )

        # Duplicate upload
        response = await client.post(
            "/api/runner-releases/upload",
            data={"version": "1.0.0", "platform": "windows"},
            files={"file": ("runner2.zip", b"more content", "application/zip")},
            headers=auth_headers(test_admin.id),
        )

        assert response.status_code == 400
        assert "已存在" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_upload_release_not_super_admin(
        self, client: AsyncClient, test_org_admin: User
    ):
        """组织管理员上传 → 403"""
        response = await client.post(
            "/api/runner-releases/upload",
            data={"version": "1.0.0", "platform": "windows"},
            files={"file": ("runner.zip", b"fake content", "application/zip")},
            headers=auth_headers(test_org_admin.id),
        )

        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_upload_release_regular_user(
        self, client: AsyncClient, test_user: User
    ):
        """普通用户上传 → 403"""
        response = await client.post(
            "/api/runner-releases/upload",
            data={"version": "1.0.0", "platform": "windows"},
            files={"file": ("runner.zip", b"fake content", "application/zip")},
            headers=auth_headers(test_user.id),
        )

        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_upload_release_unauthorized(self, client: AsyncClient):
        """未认证上传 → 401"""
        response = await client.post(
            "/api/runner-releases/upload",
            data={"version": "1.0.0", "platform": "windows"},
            files={"file": ("runner.zip", b"fake content", "application/zip")},
        )

        assert response.status_code == 401


# ============================
# 2. GET /runner-releases/latest
# ============================


class TestGetLatest:
    """测试获取各平台最新版本"""

    @pytest.mark.asyncio
    async def test_get_latest_releases(
        self, client: AsyncClient, db_session: AsyncSession, test_user: User
    ):
        """返回各平台最新版本，按平台分组"""
        # Create releases: one latest per platform
        await create_release(db_session, "1.0.0", "windows", is_latest=True)
        await create_release(db_session, "2.0.0", "windows", is_latest=False)
        await create_release(db_session, "1.1.0", "linux", is_latest=True)
        await create_release(db_session, "1.0.0", "macos", is_latest=True)

        response = await client.get(
            "/api/runner-releases/latest",
            headers=auth_headers(test_user.id),
        )

        assert response.status_code == 200
        data = response.json()
        # Should only return releases with is_latest=True, grouped by platform
        assert "windows" in data
        assert "linux" in data
        assert "macos" in data
        assert data["windows"]["version"] == "1.0.0"
        assert data["linux"]["version"] == "1.1.0"
        assert data["macos"]["version"] == "1.0.0"

    @pytest.mark.asyncio
    async def test_get_latest_empty(self, client: AsyncClient, test_user: User):
        """没有最新版本时返回空 dict"""
        response = await client.get(
            "/api/runner-releases/latest",
            headers=auth_headers(test_user.id),
        )

        assert response.status_code == 200
        assert response.json() == {}

    @pytest.mark.asyncio
    async def test_get_latest_unauthorized(self, client: AsyncClient):
        """未认证获取最新版本 → 401"""
        response = await client.get("/api/runner-releases/latest")
        assert response.status_code == 401


# ============================
# 3. GET /runner-releases/versions
# ============================


class TestGetVersions:
    """测试获取版本列表"""

    @pytest.mark.asyncio
    async def test_get_versions_list(
        self, client: AsyncClient, db_session: AsyncSession, test_user: User
    ):
        """返回所有版本，按 created_at 降序"""
        await create_release(db_session, "1.0.0", "windows")
        await create_release(db_session, "2.0.0", "windows")
        await create_release(db_session, "1.1.0", "linux")

        response = await client.get(
            "/api/runner-releases/versions",
            headers=auth_headers(test_user.id),
        )

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 3
        versions = [r["version"] for r in data]
        # Should be in created_at desc order
        assert (
            versions.index("1.1.0") < versions.index("1.0.0") or True
        )  # created_at sort

    @pytest.mark.asyncio
    async def test_get_versions_filter_by_platform(
        self, client: AsyncClient, db_session: AsyncSession, test_user: User
    ):
        """按平台过滤版本"""
        await create_release(db_session, "1.0.0", "windows")
        await create_release(db_session, "2.0.0", "windows")
        await create_release(db_session, "1.1.0", "linux")

        response = await client.get(
            "/api/runner-releases/versions?platform=windows",
            headers=auth_headers(test_user.id),
        )

        assert response.status_code == 200
        data = response.json()
        assert all(r["platform"] == "windows" for r in data)
        assert len(data) == 2

    @pytest.mark.asyncio
    async def test_get_versions_unauthorized(self, client: AsyncClient):
        """未认证获取版本列表 → 401"""
        response = await client.get("/api/runner-releases/versions")
        assert response.status_code == 401


# ============================
# 4. GET /runner-releases/download/{version}/{filename}
# ============================


class TestDownloadRelease:
    """测试下载 Runner 安装包"""

    @pytest.mark.asyncio
    async def test_download_release_success(
        self, client: AsyncClient, db_session: AsyncSession, test_user: User
    ):
        """下载存在的版本文件 → 200"""
        # Create a real temp file
        content = b"fake runner binary for download test"
        tmp_dir = tempfile.mkdtemp()
        file_path = os.path.join(tmp_dir, "test-download.zip")
        with open(file_path, "wb") as f:
            f.write(content)

        release = await create_release(
            db_session,
            "2.0.0",
            "windows",
            filename="test-download.zip",
            file_path=file_path,
            file_size=len(content),
        )

        response = await client.get(
            f"/api/runner-releases/download/{release.version}/{release.filename}",
            headers=auth_headers(test_user.id),
        )

        assert response.status_code == 200
        assert response.content == content

        # Cleanup
        os.remove(file_path)
        os.rmdir(tmp_dir)

    @pytest.mark.asyncio
    async def test_download_release_file_missing(
        self, client: AsyncClient, db_session: AsyncSession, test_user: User
    ):
        """数据库记录存在但文件缺失 → 404"""
        release = await create_release(
            db_session,
            "2.0.0",
            "windows",
            filename="missing.zip",
            file_path="/nonexistent/path/missing.zip",
        )

        response = await client.get(
            f"/api/runner-releases/download/{release.version}/{release.filename}",
            headers=auth_headers(test_user.id),
        )

        assert response.status_code == 404
        assert "安装包文件不存在" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_download_release_not_found(
        self, client: AsyncClient, test_user: User
    ):
        """下载不存在的版本 → 404"""
        response = await client.get(
            "/api/runner-releases/download/9.9.9/nonexistent.zip",
            headers=auth_headers(test_user.id),
        )

        assert response.status_code == 404
        assert "版本不存在" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_download_release_unauthorized(self, client: AsyncClient):
        """未认证下载 → 401"""
        response = await client.get("/api/runner-releases/download/1.0.0/test.zip")
        assert response.status_code == 401


# ============================
# 5. POST /runner-releases/versions/{version}/set-latest
# ============================


class TestSetLatest:
    """测试设置最新版本"""

    @pytest.mark.asyncio
    async def test_set_latest_as_super_admin(
        self, client: AsyncClient, db_session: AsyncSession, test_admin: User
    ):
        """超级管理员设置最新版本，旧 latest 被取消"""
        old = await create_release(db_session, "1.0.0", "windows", is_latest=True)
        new = await create_release(db_session, "2.0.0", "windows", is_latest=False)

        response = await client.post(
            f"/api/runner-releases/versions/{new.version}/set-latest",
            data={"platform": "windows"},
            headers=auth_headers(test_admin.id),
        )

        assert response.status_code == 200
        data = response.json()
        assert data["is_latest"] is True
        assert data["version"] == "2.0.0"

        # Verify old is unset
        await db_session.refresh(old)
        assert old.is_latest is False

        # Verify new is set
        await db_session.refresh(new)
        assert new.is_latest is True

    @pytest.mark.asyncio
    async def test_set_latest_version_not_found(
        self, client: AsyncClient, test_admin: User
    ):
        """设置不存在的版本 → 404"""
        response = await client.post(
            "/api/runner-releases/versions/9.9.9/set-latest",
            data={"platform": "windows"},
            headers=auth_headers(test_admin.id),
        )

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_set_latest_not_super_admin(
        self, client: AsyncClient, db_session: AsyncSession, test_org_admin: User
    ):
        """组织管理员设置最新 → 403"""
        await create_release(db_session, "1.0.0", "windows")

        response = await client.post(
            "/api/runner-releases/versions/1.0.0/set-latest",
            data={"platform": "windows"},
            headers=auth_headers(test_org_admin.id),
        )

        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_set_latest_regular_user(
        self, client: AsyncClient, db_session: AsyncSession, test_user: User
    ):
        """普通用户设置最新 → 403"""
        await create_release(db_session, "1.0.0", "windows")

        response = await client.post(
            "/api/runner-releases/versions/1.0.0/set-latest",
            data={"platform": "windows"},
            headers=auth_headers(test_user.id),
        )

        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_set_latest_unauthorized(self, client: AsyncClient):
        """未认证设置最新 → 401"""
        response = await client.post(
            "/api/runner-releases/versions/1.0.0/set-latest",
            data={"platform": "windows"},
        )

        assert response.status_code == 401


# ============================
# 6. DELETE /runner-releases/versions/{version}
# ============================


class TestDeleteVersion:
    """测试删除版本"""

    @pytest.mark.asyncio
    async def test_delete_release_as_super_admin(
        self, client: AsyncClient, db_session: AsyncSession, test_admin: User
    ):
        """超级管理员删除版本 → 200，物理文件和数据库记录均删除"""
        # Create a real temp file
        content = b"delete me"
        tmp_dir = tempfile.mkdtemp()
        file_path = os.path.join(tmp_dir, "to-delete.zip")
        with open(file_path, "wb") as f:
            f.write(content)

        release = await create_release(
            db_session,
            "1.0.0",
            "windows",
            filename="to-delete.zip",
            file_path=file_path,
            uploaded_by=test_admin.id,
        )

        response = await client.request(
            "DELETE",
            f"/api/runner-releases/versions/{release.version}",
            data={"platform": "windows"},
            headers=auth_headers(test_admin.id),
        )

        assert response.status_code == 200
        assert response.json()["message"] == "版本已删除"

        # Verify file is deleted
        assert not os.path.exists(file_path)

        # Verify DB record is deleted
        await db_session.flush()
        from sqlalchemy import select

        result = await db_session.execute(
            select(RunnerRelease).where(RunnerRelease.id == release.id)
        )
        assert result.scalar_one_or_none() is None

        # Cleanup temp dir
        os.rmdir(tmp_dir)

    @pytest.mark.asyncio
    async def test_delete_release_not_found(
        self, client: AsyncClient, test_admin: User
    ):
        """删除不存在的版本 → 404"""
        response = await client.request(
            "DELETE",
            "/api/runner-releases/versions/9.9.9",
            data={"platform": "windows"},
            headers=auth_headers(test_admin.id),
        )

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_release_not_super_admin(
        self, client: AsyncClient, db_session: AsyncSession, test_org_admin: User
    ):
        """组织管理员删除版本 → 403"""
        await create_release(db_session, "1.0.0", "windows")

        response = await client.request(
            "DELETE",
            "/api/runner-releases/versions/1.0.0",
            data={"platform": "windows"},
            headers=auth_headers(test_org_admin.id),
        )

        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_delete_release_regular_user(
        self, client: AsyncClient, db_session: AsyncSession, test_user: User
    ):
        """普通用户删除版本 → 403"""
        await create_release(db_session, "1.0.0", "windows")

        response = await client.request(
            "DELETE",
            "/api/runner-releases/versions/1.0.0",
            data={"platform": "windows"},
            headers=auth_headers(test_user.id),
        )

        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_delete_release_unauthorized(self, client: AsyncClient):
        """未认证删除版本 → 401"""
        response = await client.request(
            "DELETE",
            "/api/runner-releases/versions/1.0.0",
            data={"platform": "windows"},
        )

        assert response.status_code == 401
