import hashlib
import os

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_active_user
from app.core import get_db
from app.models import RunnerRelease, User
from app.schemas.runner_schemas import RunnerReleaseResponse

router = APIRouter(prefix="/runner-releases", tags=["Runner版本管理"])

# Storage directory for release files
RELEASES_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "workspace", "releases"
)


def _ensure_releases_dir():
    os.makedirs(RELEASES_DIR, exist_ok=True)


@router.get("/latest")
async def get_latest_releases(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取各平台最新版本"""
    result = await db.execute(select(RunnerRelease).where(RunnerRelease.is_latest))
    releases = result.scalars().all()

    # Group by platform
    by_platform = {}
    for r in releases:
        by_platform[r.platform] = RunnerReleaseResponse(
            id=r.id,
            version=r.version,
            filename=r.filename,
            file_size=r.file_size,
            checksum=r.checksum,
            platform=r.platform,
            release_notes=r.release_notes,
            is_latest=r.is_latest,
            uploaded_by=r.uploaded_by,
            created_at=r.created_at,
        )
    return by_platform


@router.get("/versions", response_model=list[RunnerReleaseResponse])
async def list_versions(
    platform: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取版本列表"""
    query = select(RunnerRelease).order_by(RunnerRelease.created_at.desc())
    if platform:
        query = query.where(RunnerRelease.platform == platform)
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/download/{version}/{filename}")
async def download_release(
    version: str,
    filename: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """下载 Runner 安装包"""
    result = await db.execute(
        select(RunnerRelease).where(
            RunnerRelease.version == version,
            RunnerRelease.filename == filename,
        )
    )
    release = result.scalar_one_or_none()
    if not release:
        raise HTTPException(status_code=404, detail="版本不存在")

    file_path = release.file_path
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="安装包文件不存在")

    return FileResponse(file_path, filename=filename)


@router.post(
    "/upload", response_model=RunnerReleaseResponse, status_code=status.HTTP_201_CREATED
)
async def upload_release(
    version: str = Form(...),
    platform: str = Form(...),
    release_notes: str | None = Form(None),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """上传 Runner 新版本（仅超管）"""
    if not current_user.is_super_admin:
        raise HTTPException(status_code=403, detail="仅超级管理员可上传版本")

    _ensure_releases_dir()

    # Determine file extension from uploaded file
    original_ext = os.path.splitext(file.filename or ".zip")[1] or ".zip"
    filename = f"pioneclaw-runner-{platform}-{version}{original_ext}"
    file_path = os.path.join(RELEASES_DIR, filename)

    contents = await file.read()
    file_size = len(contents)

    # Size limit: 200 MB
    MAX_UPLOAD_SIZE = 200 * 1024 * 1024
    if file_size > MAX_UPLOAD_SIZE:
        raise HTTPException(status_code=413, detail="文件大小超过 200MB 限制")

    checksum = hashlib.sha256(contents).hexdigest()

    # Check duplicate
    existing = await db.execute(
        select(RunnerRelease).where(
            RunnerRelease.version == version,
            RunnerRelease.platform == platform,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="该平台此版本已存在")

    # Save to temp file first, rename after DB insert succeeds
    tmp_path = file_path + ".tmp"
    with open(tmp_path, "wb") as f:
        f.write(contents)

    release = RunnerRelease(
        version=version,
        filename=filename,
        file_path=file_path,
        file_size=file_size,
        checksum=checksum,
        platform=platform,
        release_notes=release_notes,
        is_latest=False,
        uploaded_by=current_user.id,
    )
    db.add(release)
    try:
        await db.commit()
        os.replace(tmp_path, file_path)  # Atomic replace after DB success
    except Exception:
        # Clean up temp file on failure
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise

    await db.refresh(release)
    return release


@router.post("/versions/{version}/set-latest", response_model=RunnerReleaseResponse)
async def set_latest_version(
    version: str,
    platform: str = Form(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """设为最新版本（仅超管）"""
    if not current_user.is_super_admin:
        raise HTTPException(status_code=403, detail="仅超级管理员可设置")

    # Unset old latest for this platform
    old_result = await db.execute(
        select(RunnerRelease).where(
            RunnerRelease.platform == platform,
            RunnerRelease.is_latest,
        )
    )
    for old in old_result.scalars().all():
        old.is_latest = False

    # Set new latest
    result = await db.execute(
        select(RunnerRelease).where(
            RunnerRelease.version == version,
            RunnerRelease.platform == platform,
        )
    )
    release = result.scalar_one_or_none()
    if not release:
        raise HTTPException(status_code=404, detail="版本不存在")

    release.is_latest = True
    await db.commit()
    await db.refresh(release)
    return release


@router.delete("/versions/{version}")
async def delete_version(
    version: str,
    platform: str = Form(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """删除版本（仅超管）"""
    if not current_user.is_super_admin:
        raise HTTPException(status_code=403, detail="仅超级管理员可删除版本")

    result = await db.execute(
        select(RunnerRelease).where(
            RunnerRelease.version == version,
            RunnerRelease.platform == platform,
        )
    )
    release = result.scalar_one_or_none()
    if not release:
        raise HTTPException(status_code=404, detail="版本不存在")

    # Delete physical file
    if os.path.exists(release.file_path):
        os.remove(release.file_path)

    await db.delete(release)
    await db.commit()
    return {"message": "版本已删除"}
