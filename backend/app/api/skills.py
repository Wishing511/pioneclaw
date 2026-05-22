import io
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_active_user
from app.core import get_db
from app.core.permissions import PermissionChecker
from app.models import Approval, Skill, User
from app.modules.agent.skills import get_skills_loader
from app.modules.agent.skills_config import (
    SkillsConfigManager,
    get_config_manager,
)
from app.modules.agent.skills_schema import (
    SkillSchema,
    SkillsSchemaRegistry,
    get_schema_registry,
)
from app.schemas import MessageResponse, SkillCreate, SkillResponse, SkillUpdate

router = APIRouter(prefix="/skills", tags=["技能管理"])


@router.get("", response_model=list[SkillResponse])
async def list_skills(
    skip: int = 0,
    limit: int = 20,
    category: str | None = None,
    is_active: bool | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取技能列表"""
    from app.models.models import SkillScope

    query = select(Skill)

    if category:
        query = query.where(Skill.category == category)
    if is_active is not None:
        query = query.where(Skill.is_active == is_active)

    # 权限过滤：所有人只看到 system + 自己 org + 自己创建的 user scope
    conditions = [Skill.scope == SkillScope.SYSTEM.value]
    if current_user.organization_id:
        conditions.append(Skill.scope == SkillScope.ORG.value)
    conditions.append(
        (Skill.scope == SkillScope.USER.value) & (Skill.creator_id == current_user.id)
    )
    query = query.where(or_(*conditions))

    query = query.offset(skip).limit(limit).order_by(Skill.created_at.desc())
    result = await db.execute(query)
    db_skills = list(result.scalars().all())
    seen = {s.name for s in db_skills}

    # 合并文件系统 workspace 技能（去重：DB 已存在的优先）
    try:
        from app.modules.agent.skills import get_skills_loader

        loader = get_skills_loader()
        for name, fs in loader.skills.items():
            if name in seen:
                continue
            if fs.source not in ("workspace",):
                continue  # 只引入 workspace，builtin 和 openclaw 不引入
            seen.add(name)
            db_skills.append(
                SkillResponse(
                    name=name,
                    display_name=fs.metadata.title or name,
                    description=fs.metadata.description or "",
                    category="custom",
                    scope="user",  # 文件技能默认归属当前 workspace 用户
                    source="file",
                    content=fs.content,
                    package_type="inline",
                    package_size=len(fs.content.encode()) if fs.content else 0,
                    always_activate=fs.metadata.always,
                    skill_format="inline",
                    tags=fs.metadata.tags,
                    is_active=fs.enabled,
                    is_public=False,
                    creator_id=None,
                )
            )
    except Exception:
        pass

    return db_skills


@router.get("/{skill_id}", response_model=SkillResponse)
async def get_skill(
    skill_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取单个技能详情"""
    result = await db.execute(select(Skill).where(Skill.id == skill_id))
    skill = result.scalar_one_or_none()

    if not skill:
        raise HTTPException(status_code=404, detail="技能不存在")

    return skill


@router.get("/{skill_id}/content")
async def get_skill_content(
    skill_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取技能内容"""
    result = await db.execute(select(Skill).where(Skill.id == skill_id))
    skill = result.scalar_one_or_none()

    if not skill:
        raise HTTPException(status_code=404, detail="技能不存在")

    return {"content": skill.content or ""}


def _project_skills_dir() -> Path:
    """获取项目 skills/ 目录"""
    return Path(__file__).resolve().parents[3] / "skills"


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(PermissionChecker("skill:create"))],
)
async def create_skill(
    skill_data: SkillCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """创建技能 → 写入本地 skills/ 目录"""
    # 检查名称是否已存在（DB + 文件系统）
    result = await db.execute(select(Skill).where(Skill.name == skill_data.name))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="技能名称已在 DB 中存在")

    loader = get_skills_loader()
    if skill_data.name in loader.skills:
        raise HTTPException(status_code=400, detail="技能名称已在文件系统中存在")

    # 写入 skills/{name}/SKILL.md
    skill_dir = _project_skills_dir() / skill_data.name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(skill_data.content or "", encoding="utf-8")

    # 重新加载
    loader.reload()
    fs = loader.skills.get(skill_data.name)
    if not fs:
        raise HTTPException(status_code=500, detail="技能文件创建成功但加载失败")

    return SkillResponse(
        name=skill_data.name,
        display_name=skill_data.display_name or skill_data.name,
        description=skill_data.description or "",
        category=skill_data.category,
        scope="user",
        source="file",
        content=skill_data.content,
        always_activate=skill_data.always_activate,
        skill_format=skill_data.skill_format or "inline",
        is_active=True,
        is_public=True,
        creator_id=current_user.id,
    )


@router.put(
    "/{skill_id}",
    response_model=SkillResponse,
    dependencies=[Depends(PermissionChecker("skill:update"))],
)
async def update_skill(
    skill_id: int,
    skill_data: SkillUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """更新技能"""
    result = await db.execute(select(Skill).where(Skill.id == skill_id))
    skill = result.scalar_one_or_none()

    if not skill:
        raise HTTPException(status_code=404, detail="技能不存在")

    update_data = skill_data.model_dump(exclude_unset=True)

    # 如果更新了 content，重新计算包大小并解析 frontmatter
    if "content" in update_data and update_data["content"]:
        update_data["package_size"] = len(update_data["content"].encode())

        # 解析 frontmatter 中的 always 和 format
        if update_data["content"].startswith("---"):
            import re

            match = re.match(r"^---\n(.*?)\n---", update_data["content"], re.DOTALL)
            if match:
                yaml_content = match.group(1)
                for line in yaml_content.split("\n"):
                    if ":" in line:
                        key, value = line.split(":", 1)
                        key = key.strip()
                        value = value.strip().strip("\"'")
                        if key == "always":
                            update_data["always_activate"] = value.lower() in (
                                "true",
                                "yes",
                                "1",
                            )
                        elif key == "format":
                            update_data["skill_format"] = value

    for key, value in update_data.items():
        setattr(skill, key, value)

    await db.commit()
    await db.refresh(skill)

    return skill


@router.delete(
    "/{skill_id}",
    response_model=MessageResponse,
    dependencies=[Depends(PermissionChecker("skill:delete"))],
)
async def delete_skill(
    skill_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """删除技能"""
    result = await db.execute(select(Skill).where(Skill.id == skill_id))
    skill = result.scalar_one_or_none()

    if not skill:
        raise HTTPException(status_code=404, detail="技能不存在")

    await db.delete(skill)
    await db.commit()

    return MessageResponse(message="技能已删除")


class UpdateFileSkillRequest(BaseModel):
    content: str
    display_name: str | None = None
    description: str | None = None


@router.put("/file/{skill_name}", response_model=SkillResponse)
async def update_file_skill(
    skill_name: str,
    skill_data: UpdateFileSkillRequest,
    current_user: User = Depends(get_current_active_user),
):
    """更新文件技能（写入 SKILL.md）"""
    loader = get_skills_loader()
    fs = loader.skills.get(skill_name)
    if not fs or fs.source not in ("workspace",):
        raise HTTPException(status_code=404, detail="文件技能不存在")

    skill_dir = _project_skills_dir() / skill_name
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(skill_data.content, encoding="utf-8")
    loader.reload()

    fs = loader.skills.get(skill_name)
    return SkillResponse(
        name=skill_name,
        display_name=skill_data.display_name
        or (fs.metadata.title if fs else skill_name),
        description=skill_data.description
        or (fs.metadata.description if fs else "")
        or "",
        category="custom",
        scope="user",
        source="file",
        content=skill_data.content,
        is_active=True,
    )


@router.delete("/file/{skill_name}", response_model=MessageResponse)
async def delete_file_skill(
    skill_name: str,
    current_user: User = Depends(get_current_active_user),
):
    """删除文件技能（删除整个技能目录）"""
    loader = get_skills_loader()
    fs = loader.skills.get(skill_name)
    if not fs or fs.source not in ("workspace",):
        raise HTTPException(status_code=404, detail="文件技能不存在")

    skill_dir = _project_skills_dir() / skill_name
    if skill_dir.exists():
        shutil.rmtree(skill_dir)
    loader.reload()
    return MessageResponse(message="文件技能已删除")


@router.post("/reload", response_model=MessageResponse)
async def reload_skills(current_user: User = Depends(get_current_active_user)):
    """重新加载技能文件（热重载）"""
    try:
        from app.modules.agent.skills import get_skills_loader

        loader = get_skills_loader()
        loader.reload()
        return MessageResponse(message=f"技能已重新加载，共 {len(loader.skills)} 个")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"重载失败: {str(e)}")


@router.get("/{skill_id}/check-dependencies")
async def check_skill_dependencies(
    skill_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """检查技能依赖是否满足"""
    result = await db.execute(select(Skill).where(Skill.id == skill_id))
    skill = result.scalar_one_or_none()

    if not skill:
        raise HTTPException(status_code=404, detail="技能不存在")

    import os

    dependencies = skill.dependencies or {}
    missing = []

    # 检查二进制依赖
    for binary in dependencies.get("bins", []):
        if not shutil.which(binary):
            missing.append(
                {
                    "type": "binary",
                    "name": binary,
                    "message": f"CLI 工具未安装: {binary}",
                }
            )

    # 检查环境变量
    for env_var in dependencies.get("env", []):
        if not os.environ.get(env_var):
            missing.append(
                {
                    "type": "env",
                    "name": env_var,
                    "message": f"环境变量未设置: {env_var}",
                }
            )

    # 检查 Python 包
    for pkg in dependencies.get("python_packages", []):
        try:
            __import__(pkg)
        except ImportError:
            missing.append(
                {"type": "python", "name": pkg, "message": f"Python 包未安装: {pkg}"}
            )

    return {
        "skill_id": skill_id,
        "skill_name": skill.name,
        "satisfied": len(missing) == 0,
        "missing": missing,
    }


# ==================== 请求模型 (config/schema) ====================


class SetConfigRequest(BaseModel):
    """设置配置请求"""

    config: dict


# ==================== 依赖 (config/schema) ====================


def _get_config_mgr() -> SkillsConfigManager:
    project_root = Path(__file__).resolve().parents[3]
    skills_dir = project_root / "skills"
    return get_config_manager(skills_dir)


def _get_schema_reg() -> SkillsSchemaRegistry:
    project_root = Path(__file__).resolve().parents[3]
    skills_dir = project_root / "skills"
    return get_schema_registry(skills_dir)


# ==================== 配置管理 API (name-based) ====================


@router.get("/{name}/config")
async def get_skill_config(
    name: str,
    current_user: User = Depends(get_current_active_user),
    config_mgr: SkillsConfigManager = Depends(_get_config_mgr),
):
    """获取技能配置"""
    config = config_mgr.load_config(name)

    if config is None:
        schema_reg = _get_schema_reg()
        config = schema_reg.get_default_config(name)

    return {
        "name": name,
        "config": config or {},
    }


@router.put("/{name}/config")
async def set_skill_config(
    name: str,
    request: SetConfigRequest,
    current_user: User = Depends(get_current_active_user),
    config_mgr: SkillsConfigManager = Depends(_get_config_mgr),
    schema_reg: SkillsSchemaRegistry = Depends(_get_schema_reg),
):
    """设置技能配置"""
    is_valid, errors = schema_reg.validate_config(name, request.config)

    if not is_valid:
        raise HTTPException(status_code=400, detail={"errors": errors})

    success = config_mgr.save_config(name, request.config)

    if not success:
        raise HTTPException(
            status_code=500, detail=f"Failed to save config for skill '{name}'"
        )

    return {"success": True, "name": name}


@router.post("/{name}/config/fix")
async def fix_skill_config(
    name: str,
    current_user: User = Depends(get_current_active_user),
    config_mgr: SkillsConfigManager = Depends(_get_config_mgr),
):
    """自动修复技能配置"""
    success, changes = config_mgr.auto_fix_config(name)

    if not success:
        raise HTTPException(status_code=400, detail={"changes": changes})

    return {
        "success": True,
        "name": name,
        "changes": changes,
    }


# ==================== Schema 管理 API (name-based) ====================


@router.get("/{name}/schema")
async def get_skill_schema(
    name: str,
    current_user: User = Depends(get_current_active_user),
    schema_reg: SkillsSchemaRegistry = Depends(_get_schema_reg),
):
    """获取技能 Schema"""
    schema = schema_reg.get_schema(name)

    if schema is None:
        raise HTTPException(
            status_code=404, detail=f"Schema not found for skill '{name}'"
        )

    return schema.to_dict()


@router.post("/{name}/schema")
async def create_skill_schema(
    name: str,
    schema_data: dict,
    current_user: User = Depends(get_current_active_user),
    schema_reg: SkillsSchemaRegistry = Depends(_get_schema_reg),
):
    """创建技能 Schema"""
    try:
        schema = SkillSchema.from_dict({**schema_data, "skill_name": name})
        schema_reg.register_schema(schema)
        return {"success": True, "name": name}
    except Exception as e:
        raise HTTPException(
            status_code=400, detail=f"Failed to create schema: {str(e)}"
        )


class PromoteFileSkillRequest(BaseModel):
    scope: str = "system"  # 提升目标：system / org


@router.post("/file/{skill_name}/promote", response_model=SkillResponse)
async def promote_file_skill(
    skill_name: str,
    request: PromoteFileSkillRequest = PromoteFileSkillRequest(),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """将本地文件技能发布到 DB（超管可发布为系统级）"""
    # 检查是否已在 DB 中
    result = await db.execute(select(Skill).where(Skill.name == skill_name))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="该技能已在 DB 中存在")

    # 从文件加载器读取
    loader = get_skills_loader()
    fs = loader.skills.get(skill_name)
    if not fs:
        raise HTTPException(status_code=404, detail=f"文件技能 '{skill_name}' 不存在")

    if fs.source not in ("workspace",):
        raise HTTPException(status_code=400, detail="仅 workspace 技能可发布")

    # 权限检查：只有超管可以发布为 system，org_admin 可发布为 org
    if request.scope == "system" and current_user.role.value not in ("super_admin",):
        raise HTTPException(status_code=403, detail="仅超级管理员可发布为系统级技能")
    if request.scope == "org" and current_user.role.value not in (
        "super_admin",
        "org_admin",
    ):
        raise HTTPException(status_code=403, detail="仅管理员可发布为组织级技能")

    # 创建 DB 记录
    db_skill = Skill(
        name=skill_name,
        display_name=fs.metadata.title or skill_name,
        description=fs.metadata.description or "",
        scope=request.scope,
        content=fs.content,
        package_size=len(fs.content.encode()) if fs.content else 0,
        always_activate=fs.metadata.always,
        skill_format="inline",
        is_active=True,
        is_public=True,
        creator_id=current_user.id,
    )
    db.add(db_skill)
    await db.commit()
    await db.refresh(db_skill)
    return db_skill


@router.post("/upload-zip")
async def upload_skill_zip(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_active_user),
):
    """上传 ZIP 压缩包 → 解压到 skills/{name}/ 保留完整目录结构"""
    import io
    import zipfile

    if not file.filename or not file.filename.endswith(".zip"):
        raise HTTPException(status_code=400, detail="仅支持 ZIP 文件")

    zip_data = await file.read()
    buf = io.BytesIO(zip_data)

    try:
        zf = zipfile.ZipFile(buf)
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="无效的 ZIP 文件")

    # 从 ZIP 中找到 skill 目录名（取顶层目录或 SKILL.md 所在目录）
    skill_name = file.filename.replace(".zip", "").replace(" ", "-").lower()
    for entry in zf.namelist():
        parts = entry.split("/")
        if len(parts) > 1 and parts[1] in ("SKILL.md", "skill.md"):
            skill_name = parts[0]
            break

    if not any(e.endswith("SKILL.md") or e.endswith("skill.md") for e in zf.namelist()):
        raise HTTPException(status_code=400, detail="ZIP 中未找到 SKILL.md 文件")

    # 检查名称冲突（先重载确保缓存最新）
    loader = get_skills_loader()
    loader.reload()
    if skill_name in loader.skills:
        raise HTTPException(status_code=409, detail=f"技能 '{skill_name}' 已存在")

    # 解压到 skills/{name}/
    skill_dir = _project_skills_dir() / skill_name
    if skill_dir.exists():
        shutil.rmtree(skill_dir)
    skill_dir.mkdir(parents=True, exist_ok=True)

    # 跳过二进制文件扩展名
    skip_exts = {".pyc", ".zip", ".tar", ".gz", ".7z", ".exe", ".dll", ".so"}

    for entry in zf.namelist():
        # 安全：跳过目录、跳过以 __MACOSX 开头的、跳过隐藏文件
        if entry.endswith("/"):
            continue
        parts = entry.split("/")
        if parts[0] == "__MACOSX" or parts[0].startswith("._"):
            continue
        # 跳过二进制文件
        if any(entry.lower().endswith(ext) for ext in skip_exts):
            continue
        # 提取相对路径（跳过第一层目录）
        rel_path = "/".join(parts[1:]) if len(parts) > 1 else parts[0]
        if not rel_path:
            continue
        target = skill_dir / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(zf.read(entry))

    # 重新加载
    loader.reload()
    fs = loader.skills.get(skill_name)
    if not fs:
        raise HTTPException(status_code=500, detail="解压成功但技能加载失败")

    return SkillResponse(
        name=skill_name,
        display_name=fs.metadata.title or skill_name,
        description=fs.metadata.description or "",
        category="custom",
        scope="user",
        source="file",
        content=fs.content,
        always_activate=fs.metadata.always,
        is_active=True,
    )


# ============================================================
# 技能导入/导出/审核
# ============================================================

SKILLS_DIR = Path(__file__).parent.parent / "skills"


@router.post("/import/preview")
async def preview_import(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_active_user),
):
    """预览 Skill zip 包内容"""
    try:
        zf = zipfile.ZipFile(io.BytesIO(await file.read()))
        names = zf.namelist()
        # 查找 SKILL.md
        skill_md = None
        for n in names:
            if n.endswith("SKILL.md") or n.endswith("skill.md"):
                skill_md = zf.read(n).decode("utf-8", errors="replace")
                break
        return {
            "filename": file.filename,
            "files": names[:50],
            "file_count": len(names),
            "skill_md_preview": skill_md[:500] if skill_md else None,
        }
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="无效的 zip 文件")


@router.post("/import")
async def import_skill(
    file: UploadFile = File(...),
    scope: str = "user",
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """导入 Skill zip 包"""
    if scope not in ("user", "org", "system"):
        raise HTTPException(status_code=400, detail="scope 必须为 user/org/system")
    if (
        scope in ("org", "system")
        and not current_user.is_org_admin
        and not current_user.is_super_admin
    ):
        raise HTTPException(status_code=403, detail="需要管理员权限导入到组织/系统级别")

    try:
        zf = zipfile.ZipFile(io.BytesIO(await file.read()))
        name = file.filename.replace(".zip", "").replace(".skill", "")
        target = SKILLS_DIR / name
        if target.exists():
            import shutil

            shutil.rmtree(target)
        target.mkdir(parents=True)
        for entry in zf.namelist():
            # 安全：防止路径穿越
            safe = (
                Path(entry).name
                if "/" not in entry and "\\" not in entry
                else entry.split("/")[-1]
            )
            if not safe:
                continue
            content = zf.read(entry)
            (target / safe).write_bytes(content)
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="无效的 zip 文件")

    # Reload skills
    loader = get_skills_loader()
    loader.reload()
    fs = loader.skills.get(name)

    # Save to DB
    result = await db.execute(select(Skill).where(Skill.name == name))
    skill = result.scalar_one_or_none()
    if not skill:
        skill = Skill(
            name=name,
            display_name=fs.metadata.title if fs else name,
            description=fs.metadata.description if fs else "",
            category="custom",
            scope=scope,
            creator_id=current_user.id,
            is_active=True,
        )
        db.add(skill)
        await db.commit()
        await db.refresh(skill)
    return {"message": f"技能 {name} 导入成功", "skill_id": skill.id}


@router.post("/upload")
async def upload_skill_package(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_active_user),
):
    """上传 Skill 包到服务器"""
    if not file.filename.endswith(".zip"):
        raise HTTPException(status_code=400, detail="仅支持 .zip 文件")
    dest = SKILLS_DIR / "uploads" / file.filename
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(await file.read())
    return {"message": "上传成功", "path": str(dest.relative_to(SKILLS_DIR))}


@router.get("/{skill_id}/download")
async def download_skill(
    skill_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """下载 Skill 为 zip 包"""
    from fastapi.responses import FileResponse

    result = await db.execute(select(Skill).where(Skill.id == skill_id))
    skill = result.scalar_one_or_none()
    if not skill:
        raise HTTPException(status_code=404, detail="技能不存在")

    zip_path = SKILLS_DIR / f"{skill.name}.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        skill_dir = SKILLS_DIR / skill.name
        if skill_dir.exists():
            for f in skill_dir.rglob("*"):
                if f.is_file():
                    zf.write(f, f.relative_to(skill_dir))
    return FileResponse(
        zip_path, filename=f"{skill.name}.zip", media_type="application/zip"
    )


@router.get("/reviews/pending")
async def list_pending_reviews(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取待审核技能列表"""
    if not current_user.is_org_admin and not current_user.is_super_admin:
        raise HTTPException(status_code=403, detail="需要管理员权限")
    result = await db.execute(
        select(Approval)
        .where(
            Approval.approval_type == "skill_to_org",
            Approval.status == "pending",
        )
        .order_by(Approval.created_at.desc())
    )
    approvals = result.scalars().all()
    return [
        {
            "id": a.id,
            "title": a.title,
            "requester_id": a.requester_id,
            "resource_id": a.resource_id,
            "created_at": a.created_at.isoformat(),
        }
        for a in approvals
    ]


@router.post("/reviews/{review_id}")
async def review_skill(
    review_id: int,
    approve: bool = True,
    reject_reason: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """审批技能"""
    if not current_user.is_org_admin and not current_user.is_super_admin:
        raise HTTPException(status_code=403, detail="需要管理员权限")
    approval = await db.get(Approval, review_id)
    if not approval:
        raise HTTPException(status_code=404, detail="审批不存在")
    if approve:
        approval.status = "approved"
        # 提升技能 scope 到 org
        res = await db.execute(select(Skill).where(Skill.name == approval.resource_id))
        skill = res.scalar_one_or_none()
        if skill:
            skill.scope = "org"
    else:
        approval.status = "rejected"
        approval.review_comment = reject_reason
    approval.reviewer_id = current_user.id
    approval.reviewed_at = datetime.now(tz=timezone.utc)
    await db.commit()
    return {"message": "审批完成", "status": approval.status}


# ============================================================
# Skill 文件管理
# ============================================================


@router.get("/{skill_id}/files")
async def list_skill_files(
    skill_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取技能文件列表"""
    result = await db.execute(select(Skill).where(Skill.id == skill_id))
    skill = result.scalar_one_or_none()
    if not skill:
        raise HTTPException(status_code=404, detail="技能不存在")
    skill_dir = SKILLS_DIR / skill.name
    if not skill_dir.exists():
        return {"skill_id": skill_id, "files": []}
    files = []
    for f in skill_dir.rglob("*"):
        if f.is_file():
            rel = str(f.relative_to(skill_dir)).replace("\\", "/")
            files.append({"path": rel, "size": f.stat().st_size})
    return {"skill_id": skill_id, "skill_name": skill.name, "files": files}


@router.get("/{skill_id}/files/{path:path}")
async def get_skill_file(
    skill_id: int,
    path: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取技能文件内容"""
    result = await db.execute(select(Skill).where(Skill.id == skill_id))
    skill = result.scalar_one_or_none()
    if not skill:
        raise HTTPException(status_code=404, detail="技能不存在")
    file_path = SKILLS_DIR / skill.name / path
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    return {"path": path, "content": file_path.read_text("utf-8", errors="replace")}


@router.put("/{skill_id}/files/{path:path}")
async def put_skill_file(
    skill_id: int,
    path: str,
    content: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """创建/更新技能文件"""
    result = await db.execute(select(Skill).where(Skill.id == skill_id))
    skill = result.scalar_one_or_none()
    if not skill:
        raise HTTPException(status_code=404, detail="技能不存在")
    file_path = SKILLS_DIR / skill.name / path
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")
    return {"message": "保存成功", "path": path}
