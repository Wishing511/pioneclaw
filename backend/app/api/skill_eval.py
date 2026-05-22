"""Skill Evaluation API — 技能评估与优化"""

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_active_user
from app.core import get_db
from app.models import Skill as DBSkill
from app.models import User
from app.modules.agent.skills import get_skills_loader

router = APIRouter(prefix="/skill-eval", tags=["技能评估"])


class FileEntry(BaseModel):
    name: str
    path: str
    type: str  # "file" | "dir"
    size: int = 0
    children: list["FileEntry"] | None = None


class SkillInfo(BaseModel):
    id: int | None = None
    name: str
    display_name: str = ""
    description: str = ""
    source: str = ""  # "db" | "file"
    scope: str = ""
    enabled: bool = True
    skill_format: str = "inline"


BINARY_EXTENSIONS = {
    ".pyc",
    ".pyo",
    ".zip",
    ".tar",
    ".gz",
    ".bz2",
    ".xz",
    ".7z",
    ".exe",
    ".dll",
    ".so",
    ".pyd",
    ".dylib",
    ".db",
    ".sqlite",
    ".sqlite3",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".ico",
    ".svg",
    ".webp",
    ".woff",
    ".woff2",
    ".ttf",
    ".otf",
    ".eot",
    ".mp3",
    ".mp4",
    ".wav",
    ".avi",
    ".mov",
    ".webm",
    ".bin",
    ".dat",
    ".pickle",
    ".pkl",
    ".pth",
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
}


def _is_text_file(path: Path) -> bool:
    """判断是否为文本文件（排除二进制）"""
    if path.suffix.lower() in BINARY_EXTENSIONS:
        return False
    # 无后缀或已知文本后缀直接放行
    text_extensions = {
        ".md",
        ".py",
        ".js",
        ".ts",
        ".jsx",
        ".tsx",
        ".vue",
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".ini",
        ".cfg",
        ".html",
        ".css",
        ".scss",
        ".less",
        ".xml",
        ".svg",
        ".sh",
        ".bat",
        ".ps1",
        ".txt",
        ".rst",
        ".csv",
        ".sql",
        ".go",
        ".rs",
        ".java",
        ".c",
        ".cpp",
        ".h",
        ".rb",
        ".php",
        ".swift",
        ".kt",
        ".scala",
    }
    if path.suffix.lower() in text_extensions:
        return True
    # 无后缀或无明确类型：尝试读前 256 字节判断
    try:
        with open(path, "rb") as f:
            chunk = f.read(256)
        return b"\x00" not in chunk  # 二进制文件通常含空字节
    except (OSError, PermissionError):
        return False


def _walk_dir(root: Path, base: Path) -> list[FileEntry]:
    """递归遍历目录，返回文件树"""
    entries: list[FileEntry] = []
    try:
        items = sorted(root.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
    except (OSError, PermissionError):
        return entries

    for item in items:
        if item.is_dir():
            rel = str(item.relative_to(base)).replace("\\", "/")
            entries.append(
                FileEntry(
                    name=item.name,
                    path=rel,
                    type="dir",
                    children=_walk_dir(item, base),
                )
            )
        elif _is_text_file(item):
            rel = str(item.relative_to(base)).replace("\\", "/")
            entries.append(
                FileEntry(
                    name=item.name,
                    path=rel,
                    type="file",
                    size=item.stat().st_size,
                )
            )
    return entries


@router.get("/skills", response_model=list[SkillInfo])
async def list_eval_skills(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """列出所有技能（DB + 文件系统），供评估页使用"""
    skills: list[SkillInfo] = []
    seen: set[str] = set()

    # 1. DB 技能
    from app.models.models import SkillScope

    conditions = [DBSkill.scope == SkillScope.SYSTEM.value]
    if current_user.organization_id:
        conditions.append(DBSkill.scope == SkillScope.ORG.value)
    conditions.append(
        (DBSkill.scope == SkillScope.USER.value)
        & (DBSkill.creator_id == current_user.id)
    )
    from sqlalchemy import or_

    result = await db.execute(
        select(DBSkill).where(or_(*conditions)).order_by(DBSkill.name)
    )
    for s in result.scalars().all():
        skills.append(
            SkillInfo(
                id=s.id,
                name=s.name,
                display_name=s.display_name or s.name,
                description=s.description or "",
                source="db",
                scope=s.scope,
                enabled=s.is_active,
                skill_format=s.skill_format or "inline",
            )
        )
        seen.add(s.name)

    # 2. 文件系统技能（仅 workspace 用户技能，排除 builtin 和 openclaw）
    try:
        loader = get_skills_loader()
        for name, skill in loader.skills.items():
            if name in seen:
                continue
            if skill.source in ("openclaw", "builtin"):
                continue  # 不引入外部技能和内置技能
            skills.append(
                SkillInfo(
                    name=name,
                    display_name=skill.metadata.title or name,
                    description=skill.metadata.description or "",
                    source="file",
                    scope="system",  # center 端 skills/ 即为系统级
                    enabled=skill.enabled,
                )
            )
    except Exception:
        pass

    return skills


@router.get("/skills/{skill_name}/tree")
async def get_skill_tree(
    skill_name: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取技能的文件树和内容"""
    # 先尝试文件系统
    loader = get_skills_loader()
    if skill_name in loader.skills:
        skill = loader.skills[skill_name]
        skill_dir = skill.path.parent  # SKILL.md 所在目录
        tree = _walk_dir(skill_dir, skill_dir)
        return {
            "skill_name": skill_name,
            "display_name": skill.metadata.title or skill_name,
            "source": "file",
            "skill_dir": str(skill_dir),
            "tree": [
                FileEntry(
                    name="SKILL.md",
                    path="SKILL.md",
                    type="file",
                    size=skill.path.stat().st_size if skill.path.exists() else 0,
                )
            ]
            + [f for f in tree if f.name != "SKILL.md"],
        }

    # 再尝试 DB
    from sqlalchemy import or_

    conditions = [DBSkill.name == skill_name]
    result = await db.execute(select(DBSkill).where(or_(*conditions)))
    db_skill = result.scalar_one_or_none()

    if not db_skill:
        raise HTTPException(status_code=404, detail=f"技能 '{skill_name}' 不存在")

    # DB 技能只有 content 字段，虚拟为单个 SKILL.md 文件
    return {
        "skill_name": skill_name,
        "display_name": db_skill.display_name or skill_name,
        "source": "db",
        "skill_id": db_skill.id,
        "skill_dir": None,
        "tree": [
            FileEntry(
                name="SKILL.md",
                path="SKILL.md",
                type="file",
                size=len(db_skill.content.encode()) if db_skill.content else 0,
            ),
        ],
        "content": db_skill.content or "",
    }


@router.get("/skills/{skill_name}/file")
async def get_skill_file(
    skill_name: str,
    path: str = "",
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """读取技能目录下的某个文件内容"""
    loader = get_skills_loader()

    if skill_name in loader.skills:
        skill = loader.skills[skill_name]
        skill_dir = skill.path.parent

        # 安全检查：防止路径遍历
        target = (skill_dir / path).resolve()
        if not str(target).startswith(str(skill_dir.resolve())):
            raise HTTPException(status_code=403, detail="路径越权")

        if not target.exists():
            raise HTTPException(status_code=404, detail="文件不存在")

        if target.is_dir():
            raise HTTPException(status_code=400, detail="不能读取目录")

        content = target.read_text(encoding="utf-8", errors="replace")
        return {
            "path": path or "SKILL.md",
            "content": content,
            "size": target.stat().st_size,
        }

    # DB 技能
    from sqlalchemy import or_

    conditions = [DBSkill.name == skill_name]
    result = await db.execute(select(DBSkill).where(or_(*conditions)))
    db_skill = result.scalar_one_or_none()

    if not db_skill:
        raise HTTPException(status_code=404, detail=f"技能 '{skill_name}' 不存在")

    return {
        "path": "SKILL.md",
        "content": db_skill.content or "",
        "size": len(db_skill.content.encode()) if db_skill.content else 0,
    }
