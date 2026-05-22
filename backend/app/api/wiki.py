"""
Wiki 知识库 API
"""

import re

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import PlainTextResponse
from loguru import logger
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.api.auth import get_current_active_user
from app.core.database import get_db
from app.models import Approval, User, Wiki, WikiSpace, WikiVersion
from app.models.wiki import WikiScope
from app.schemas.wiki import (
    WikiChunkRequest,
    WikiChunkResponse,
    WikiCreate,
    WikiDetail,
    WikiImportRequest,
    WikiInDB,
    WikiListResponse,
    WikiSearchResponse,
    WikiSearchResult,
    WikiSemanticSearchRequest,
    WikiSemanticSearchResponse,
    WikiSemanticSearchResult,
    WikiTree,
    WikiUpdate,
    WikiVersionInDB,
    WikiVersionListResponse,
)

router = APIRouter(prefix="/wiki", tags=["Wiki管理"])


def can_access_wiki(user: User, wiki: Wiki, action: str = "read") -> bool:
    """检查用户对 Wiki 的访问权限

    权限矩阵：
    | 资源 | 超管 | 组织管理员 | 普通用户 |
    |------|------|-----------|---------|
    | 系统级 Wiki | CRUD | R | R |
    | 组织级 Wiki | CRUD | CRUD | R |
    | 用户级 Wiki | R（全局） | R（本组织） | CRUD（自己的） |
    """
    if user.is_super_admin:
        return True

    if wiki.scope == WikiScope.SYSTEM.value:
        # 系统级：只有超管可以写，其他人只读
        return action == "read"

    if wiki.scope == WikiScope.ORG.value:
        # 组织级：本组织管理员可写，本组织用户只读
        if user.organization_id != wiki.organization_id:
            return action == "read"  # 非本组织用户只读
        if user.is_org_admin:
            return True
        return action == "read"

    if wiki.scope == WikiScope.USER.value:
        # 用户级：创建者可写，组织管理员可读
        if wiki.created_by == user.id:
            return True
        if user.is_org_admin and user.organization_id:
            # 组织管理员可读本组织用户的 Wiki
            if wiki.author and wiki.author.organization_id == user.organization_id:
                return action == "read"
        return action == "read"  # 其他用户只读（或根据需求禁止）

    return False


def filter_wikis_by_permission(user: User, query):
    """根据用户权限过滤 Wiki 查询

    所有用户（含超管）只能看到：
    - 所有系统级 Wiki
    - 本组织的组织级 Wiki
    - 自己创建的用户级 Wiki（未提交的 private wiki 对其他用户不可见）
    """
    conditions = [
        Wiki.scope == WikiScope.SYSTEM.value,
    ]

    if user.organization_id:
        conditions.append(
            (Wiki.scope == WikiScope.ORG.value)
            & (Wiki.organization_id == user.organization_id)
        )

    conditions.append(
        (Wiki.scope == WikiScope.USER.value) & (Wiki.created_by == user.id)
    )

    return query.where(or_(*conditions))


def build_wiki_tree(wikis: list[Wiki], parent_id: str = None) -> list[WikiTree]:
    """构建 Wiki 树"""
    tree = []
    children = [w for w in wikis if w.parent_id == parent_id]
    for wiki in children:
        node = WikiTree(
            id=wiki.id,
            title=wiki.title,
            content=wiki.content,
            path=wiki.path,
            parent_id=wiki.parent_id,
            tags=wiki.tags,
            created_by=wiki.created_by,
            organization_id=wiki.organization_id,
            version=wiki.version,
            status=wiki.status,
            doc_type=wiki.doc_type,
            source=wiki.source,
            chunk_count=wiki.chunk_count,
            is_indexed=wiki.is_indexed,
            scope=wiki.scope,
            meta_data=wiki.meta_data,
            created_at=wiki.created_at,
            updated_at=wiki.updated_at,
        )
        node.children = build_wiki_tree(wikis, wiki.id)
        tree.append(node)
    return tree


def extract_title_from_markdown(content: str) -> str:
    """从 Markdown 内容提取标题"""
    match = re.match(r"^#\s+(.+)", content)
    if match:
        return match.group(1).strip()
    return "Untitled"


@router.get("/", response_model=WikiListResponse)
async def list_wikis(
    organization_id: str | None = None,
    status: str | None = None,
    tag: str | None = None,
    scope: str | None = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取 Wiki 列表"""
    query = select(Wiki)
    count_query = select(func.count()).select_from(Wiki)

    # 权限过滤
    query = filter_wikis_by_permission(current_user, query)
    count_query = filter_wikis_by_permission(current_user, count_query)

    if organization_id:
        query = query.where(Wiki.organization_id == organization_id)
        count_query = count_query.where(Wiki.organization_id == organization_id)
    if status:
        query = query.where(Wiki.status == status)
        count_query = count_query.where(Wiki.status == status)
    if tag:
        # SQLite JSON 查询适配
        query = query.where(Wiki.tags.contains([tag]))
        count_query = count_query.where(Wiki.tags.contains([tag]))
    if scope:
        query = query.where(Wiki.scope == scope)
        count_query = count_query.where(Wiki.scope == scope)

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    query = query.order_by(Wiki.updated_at.desc()).offset(skip).limit(limit)
    result = await db.execute(query)
    wikis = result.scalars().all()

    return WikiListResponse(
        items=[WikiInDB.model_validate(w) for w in wikis],
        total=total,
    )


@router.get("/tree", response_model=list[WikiTree])
async def get_wiki_tree(
    organization_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取 Wiki 树"""
    query = select(Wiki).order_by(Wiki.path)
    # 权限过滤
    query = filter_wikis_by_permission(current_user, query)
    if organization_id:
        query = query.where(Wiki.organization_id == organization_id)

    result = await db.execute(query)
    wikis = result.scalars().all()
    return build_wiki_tree(list(wikis))


@router.get("/search", response_model=WikiSearchResponse)
async def search_wikis(
    q: str = Query(..., min_length=1, description="搜索关键词"),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """搜索 Wiki"""
    # 简单的 LIKE 搜索，后续可替换为 Elasticsearch
    query = (
        select(Wiki)
        .where((Wiki.title.contains(q)) | (Wiki.content.contains(q)))
        .where(Wiki.status == "published")
    )
    # 权限过滤
    query = filter_wikis_by_permission(current_user, query)

    count_query = (
        select(func.count())
        .select_from(Wiki)
        .where((Wiki.title.contains(q)) | (Wiki.content.contains(q)))
        .where(Wiki.status == "published")
    )
    count_query = filter_wikis_by_permission(current_user, count_query)

    total = (await db.execute(count_query)).scalar() or 0

    result = await db.execute(query.offset(skip).limit(limit))
    wikis = result.scalars().all()

    items = []
    for w in wikis:
        # 简单高亮：截取关键词附近的文本
        content_lower = w.content.lower()
        idx = content_lower.find(q.lower())
        highlight = None
        if idx >= 0:
            start = max(0, idx - 50)
            end = min(len(w.content), idx + len(q) + 50)
            highlight = w.content[start:end]
            if start > 0:
                highlight = "..." + highlight
            if end < len(w.content):
                highlight = highlight + "..."

        items.append(
            WikiSearchResult(
                id=w.id,
                title=w.title,
                path=w.path,
                highlight=highlight,
            )
        )

    return WikiSearchResponse(items=items, total=total)


@router.post("/", response_model=WikiInDB, status_code=status.HTTP_201_CREATED)
async def create_wiki(
    data: WikiCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """创建 Wiki"""
    # 检查路径唯一性
    result = await db.execute(select(Wiki).where(Wiki.path == data.path))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Wiki 路径已存在")

    # 确定 scope
    scope = data.scope or WikiScope.USER.value
    if scope == WikiScope.SYSTEM.value and not current_user.is_super_admin:
        raise HTTPException(status_code=403, detail="只有超管可以创建系统级 Wiki")
    if scope == WikiScope.ORG.value:
        if not current_user.is_org_admin and not current_user.is_super_admin:
            raise HTTPException(
                status_code=403, detail="只有组织管理员可以创建组织级 Wiki"
            )
        if not data.organization_id and not current_user.organization_id:
            raise HTTPException(status_code=400, detail="组织级 Wiki 需要指定组织")

    wiki = Wiki(
        title=data.title,
        content=data.content,
        path=data.path,
        parent_id=data.parent_id,
        tags=data.tags,
        created_by=current_user.id,
        organization_id=data.organization_id or current_user.organization_id,
        status=data.status,
        doc_type=data.doc_type,
        source=data.source,
        scope=scope,
    )
    db.add(wiki)
    await db.commit()
    await db.refresh(wiki)
    return wiki


@router.post("/import", response_model=WikiInDB, status_code=status.HTTP_201_CREATED)
async def import_wiki(
    data: WikiImportRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """导入 Markdown 文件"""
    # 检查路径唯一性
    result = await db.execute(select(Wiki).where(Wiki.path == data.path))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Wiki 路径已存在")

    title = data.title or extract_title_from_markdown(data.content)

    wiki = Wiki(
        title=title,
        content=data.content,
        path=data.path,
        tags=data.tags,
        created_by=current_user.id,
        organization_id=current_user.organization_id,
    )
    db.add(wiki)
    await db.commit()
    await db.refresh(wiki)
    return wiki


@router.post("/parse-file")
async def parse_wiki_file(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_active_user),
):
    """解析上传的文件（md/txt/docx/pdf），返回提取的文本和标题"""
    import os
    from io import BytesIO

    filename = file.filename or "untitled"
    ext = os.path.splitext(filename)[1].lower()
    content = ""

    try:
        file_bytes = await file.read()

        if ext in (".md", ".txt", ".markdown"):
            content = file_bytes.decode("utf-8")

        elif ext == ".docx":
            from docx import Document

            doc = Document(BytesIO(file_bytes))
            paragraphs = []
            for p in doc.paragraphs:
                if p.text.strip():
                    # Preserve heading levels
                    if p.style.name.startswith("Heading"):
                        level = (
                            int(p.style.name.split()[-1])
                            if p.style.name.split()[-1].isdigit()
                            else 1
                        )
                        paragraphs.append("#" * level + " " + p.text.strip())
                    else:
                        paragraphs.append(p.text.strip())
            content = "\n\n".join(paragraphs)

        elif ext == ".pdf":
            from PyPDF2 import PdfReader

            reader = PdfReader(BytesIO(file_bytes))
            pages = []
            for page in reader.pages:
                text = page.extract_text()
                if text:
                    pages.append(text)
            content = "\n\n".join(pages)

        else:
            raise HTTPException(status_code=400, detail=f"不支持的文件格式: {ext}")

    except Exception as e:
        raise HTTPException(status_code=400, detail=f"文件解析失败: {str(e)}")

    # Extract title from first markdown heading
    title = ""
    match = re.match(r"^#\s*(.+)$", content, re.MULTILINE)
    if match:
        title = match.group(1).strip()

    return {"title": title, "content": content, "filename": filename}


@router.get("/approvals")
async def list_wiki_approvals(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取 Wiki 审批列表"""
    result = await db.execute(
        select(Approval)
        .where(
            Approval.approval_type == "task_approval", Approval.resource_type == "wiki"
        )
        .order_by(Approval.created_at.desc())
    )
    approvals = result.scalars().all()
    return [
        {
            "id": a.id,
            "title": a.title,
            "status": a.status,
            "requester_id": a.requester_id,
            "created_at": a.created_at.isoformat(),
        }
        for a in approvals
    ]


@router.get("/{wiki_id}", response_model=WikiDetail)
async def get_wiki(
    wiki_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取 Wiki 详情"""
    result = await db.execute(
        select(Wiki).where(Wiki.id == wiki_id).options(joinedload(Wiki.author))
    )
    wiki = result.scalar_one_or_none()
    if not wiki:
        raise HTTPException(status_code=404, detail="Wiki 不存在")

    # 权限检查
    if not can_access_wiki(current_user, wiki, "read"):
        raise HTTPException(status_code=403, detail="无权访问此 Wiki")

    detail = WikiDetail.model_validate(wiki)

    # 获取作者名
    if wiki.author:
        detail.author_name = wiki.author.display_name or wiki.author.username

    return detail


@router.put("/{wiki_id}", response_model=WikiInDB)
async def update_wiki(
    wiki_id: str,
    data: WikiUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """更新 Wiki"""
    result = await db.execute(
        select(Wiki).where(Wiki.id == wiki_id).options(joinedload(Wiki.author))
    )
    wiki = result.scalar_one_or_none()
    if not wiki:
        raise HTTPException(status_code=404, detail="Wiki 不存在")

    # 权限检查：需要写权限
    if not can_access_wiki(current_user, wiki, "write"):
        raise HTTPException(status_code=403, detail="无权编辑此 Wiki")

    # 如果要修改 scope，需要检查权限
    if data.scope and data.scope != wiki.scope:
        if data.scope == WikiScope.SYSTEM.value and not current_user.is_super_admin:
            raise HTTPException(
                status_code=403, detail="只有超管可以将 Wiki 提升为系统级"
            )
        if data.scope == WikiScope.ORG.value and not (
            current_user.is_org_admin or current_user.is_super_admin
        ):
            raise HTTPException(
                status_code=403, detail="只有组织管理员可以将 Wiki 提升为组织级"
            )

    # 保存当前版本到历史
    version = wiki.create_version(
        user_id=current_user.id,
        change_summary=data.change_summary,
    )
    db.add(version)

    # 更新字段
    update_data = data.model_dump(exclude_unset=True)
    update_data.pop("change_summary", None)
    for key, value in update_data.items():
        setattr(wiki, key, value)

    # 递增版本号
    wiki.version += 1

    await db.commit()
    await db.refresh(wiki)
    return wiki


@router.delete("/{wiki_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_wiki(
    wiki_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """删除 Wiki"""
    result = await db.execute(
        select(Wiki).where(Wiki.id == wiki_id).options(joinedload(Wiki.author))
    )
    wiki = result.scalar_one_or_none()
    if not wiki:
        raise HTTPException(status_code=404, detail="Wiki 不存在")

    # 权限检查：需要写权限
    if not can_access_wiki(current_user, wiki, "write"):
        raise HTTPException(status_code=403, detail="无权删除此 Wiki")

    # 检查是否有子 Wiki
    result = await db.execute(select(Wiki).where(Wiki.parent_id == wiki_id).limit(1))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="存在子文档，不可删除")

    await db.delete(wiki)
    await db.commit()


@router.get("/{wiki_id}/history", response_model=WikiVersionListResponse)
async def get_wiki_history(
    wiki_id: str,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取 Wiki 版本历史"""
    # 检查 Wiki 存在
    result = await db.execute(select(Wiki).where(Wiki.id == wiki_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Wiki 不存在")

    count_result = await db.execute(
        select(func.count())
        .select_from(WikiVersion)
        .where(WikiVersion.wiki_id == wiki_id)
    )
    total = count_result.scalar() or 0

    result = await db.execute(
        select(WikiVersion)
        .where(WikiVersion.wiki_id == wiki_id)
        .options(joinedload(WikiVersion.author))
        .order_by(WikiVersion.version.desc())
        .offset(skip)
        .limit(limit)
    )
    versions = result.scalars().all()

    items = []
    for v in versions:
        item = WikiVersionInDB.model_validate(v)
        if v.author:
            item.author_name = v.author.display_name or v.author.username
        items.append(item)

    return WikiVersionListResponse(items=items, total=total)


@router.post("/{wiki_id}/restore/{version}", response_model=WikiInDB)
async def restore_wiki_version(
    wiki_id: str,
    version: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """恢复 Wiki 到指定版本"""
    result = await db.execute(select(Wiki).where(Wiki.id == wiki_id))
    wiki = result.scalar_one_or_none()
    if not wiki:
        raise HTTPException(status_code=404, detail="Wiki 不存在")

    # 查找目标版本
    result = await db.execute(
        select(WikiVersion).where(
            WikiVersion.wiki_id == wiki_id, WikiVersion.version == version
        )
    )
    target_version = result.scalar_one_or_none()
    if not target_version:
        raise HTTPException(status_code=404, detail="版本不存在")

    # 保存当前版本
    current_version = wiki.create_version(
        user_id=current_user.id,
        change_summary=f"恢复到版本 {version}",
    )
    db.add(current_version)

    # 恢复内容
    wiki.title = target_version.title
    wiki.content = target_version.content
    wiki.version += 1

    await db.commit()
    await db.refresh(wiki)
    return wiki


# ==================== 语义搜索 ====================


@router.post("/search/semantic", response_model=WikiSemanticSearchResponse)
async def semantic_search_wikis(
    data: WikiSemanticSearchRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    语义搜索 Wiki

    使用向量相似度搜索，需要向量存储支持
    """
    try:
        from app.modules.agent.vector_store import VectorStore

        # 初始化向量存储
        store = VectorStore()

        # 执行语义搜索
        results = await store.search(
            query=data.query,
            source_type="wiki",
            top_k=data.top_k,
        )

        items = []
        for r in results:
            if r.get("score", 0) >= data.threshold:
                # 根据 source_id 获取 Wiki 信息
                wiki_result = await db.execute(
                    select(Wiki).where(Wiki.id == r.get("source_id"))
                )
                wiki = wiki_result.scalar_one_or_none()
                if wiki:
                    items.append(
                        WikiSemanticSearchResult(
                            id=wiki.id,
                            title=wiki.title,
                            path=wiki.path,
                            content_snippet=r.get("content", "")[:200],
                            score=r.get("score", 0),
                            doc_type=wiki.doc_type,
                            tags=wiki.tags or [],
                        )
                    )

        return WikiSemanticSearchResponse(items=items, total=len(items))

    except Exception as e:
        logger.error(f"Semantic search failed: {e}")
        # 降级到关键词搜索
        return await _keyword_search_fallback(data, db)


async def _keyword_search_fallback(
    data: WikiSemanticSearchRequest,
    db: AsyncSession,
) -> WikiSemanticSearchResponse:
    """关键词搜索降级"""
    query = (
        select(Wiki)
        .where((Wiki.title.contains(data.query)) | (Wiki.content.contains(data.query)))
        .where(Wiki.status == "published")
        .limit(data.top_k)
    )

    result = await db.execute(query)
    wikis = result.scalars().all()

    items = []
    for wiki in wikis:
        # 简单计算相关度
        score = 0.5
        if data.query.lower() in wiki.title.lower():
            score = 0.9
        elif data.query.lower() in wiki.content.lower():
            score = 0.7

        items.append(
            WikiSemanticSearchResult(
                id=wiki.id,
                title=wiki.title,
                path=wiki.path,
                content_snippet=wiki.content[:200],
                score=score,
                doc_type=wiki.doc_type,
                tags=wiki.tags or [],
            )
        )

    return WikiSemanticSearchResponse(items=items, total=len(items))


# ==================== 分块管理 ====================


@router.post("/{wiki_id}/chunks", response_model=WikiChunkResponse)
async def chunk_wiki(
    wiki_id: str,
    data: WikiChunkRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    对 Wiki 内容进行分块

    返回分块列表，可选择是否索引到向量库
    """
    result = await db.execute(select(Wiki).where(Wiki.id == wiki_id))
    wiki = result.scalar_one_or_none()
    if not wiki:
        raise HTTPException(status_code=404, detail="Wiki 不存在")

    content = wiki.content
    chunks = []

    # 简单分块算法：按段落分割，然后合并到目标大小
    paragraphs = content.split("\n\n")
    current_chunk = ""
    chunk_index = 0
    start_pos = 0

    for para in paragraphs:
        if len(current_chunk) + len(para) > data.chunk_size and current_chunk:
            # 保存当前块
            chunks.append(
                {
                    "index": chunk_index,
                    "content": current_chunk.strip(),
                    "start": start_pos,
                    "end": start_pos + len(current_chunk),
                }
            )
            # 保留重叠部分
            if data.chunk_overlap > 0 and len(current_chunk) > data.chunk_overlap:
                current_chunk = current_chunk[-data.chunk_overlap :] + "\n\n" + para
            else:
                current_chunk = para
            start_pos += len(current_chunk) - len(para)
            chunk_index += 1
        else:
            current_chunk += ("\n\n" if current_chunk else "") + para

    # 最后一块
    if current_chunk.strip():
        chunks.append(
            {
                "index": chunk_index,
                "content": current_chunk.strip(),
                "start": start_pos,
                "end": start_pos + len(current_chunk),
            }
        )

    # 更新分块数
    wiki.chunk_count = len(chunks)
    await db.commit()

    return WikiChunkResponse(
        wiki_id=wiki_id,
        chunk_count=len(chunks),
        chunks=chunks,
    )


@router.post("/{wiki_id}/index", response_model=WikiInDB)
async def index_wiki_to_vector_store(
    wiki_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    将 Wiki 索引到向量库

    自动分块并索引到向量存储
    """
    result = await db.execute(select(Wiki).where(Wiki.id == wiki_id))
    wiki = result.scalar_one_or_none()
    if not wiki:
        raise HTTPException(status_code=404, detail="Wiki 不存在")

    try:
        from app.modules.agent.vector_store import VectorStore

        # 初始化向量存储
        store = VectorStore()

        # 分块
        chunk_data = WikiChunkRequest()
        chunks_result = await chunk_wiki(wiki_id, chunk_data, db, current_user)

        # 索引每个分块
        for chunk in chunks_result.chunks:
            await store.add(
                content=chunk["content"],
                source_type="wiki",
                source_id=wiki.id,
                metadata={
                    "title": wiki.title,
                    "path": wiki.path,
                    "chunk_index": chunk["index"],
                    "doc_type": wiki.doc_type,
                },
            )

        # 更新索引状态
        wiki.is_indexed = True
        await db.commit()
        await db.refresh(wiki)

        logger.info(f"Wiki {wiki_id} indexed with {len(chunks_result.chunks)} chunks")
        return wiki

    except Exception as e:
        logger.error(f"Failed to index wiki: {e}")
        raise HTTPException(status_code=500, detail=f"索引失败: {str(e)}")


@router.delete("/{wiki_id}/index", response_model=WikiInDB)
async def remove_wiki_from_index(
    wiki_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    从向量库中移除 Wiki
    """
    result = await db.execute(select(Wiki).where(Wiki.id == wiki_id))
    wiki = result.scalar_one_or_none()
    if not wiki:
        raise HTTPException(status_code=404, detail="Wiki 不存在")

    try:
        from app.modules.agent.vector_store import VectorStore, VectorStoreConfig

        config = VectorStoreConfig()
        store = VectorStore(config)

        # 删除所有相关向量
        await store.delete_by_source(source_type="wiki", source_id=wiki_id)

        wiki.is_indexed = False
        await db.commit()
        await db.refresh(wiki)

        return wiki

    except Exception as e:
        logger.error(f"Failed to remove wiki from index: {e}")
        raise HTTPException(status_code=500, detail=f"移除失败: {str(e)}")


# ==================== 知识图谱集成 ====================


@router.post("/{wiki_id}/graph", response_model=dict)
async def index_wiki_to_graph(
    wiki_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    将 Wiki 索引到知识图谱

    使用 GraphRAG 进行实体和关系抽取
    """
    result = await db.execute(select(Wiki).where(Wiki.id == wiki_id))
    wiki = result.scalar_one_or_none()
    if not wiki:
        raise HTTPException(status_code=404, detail="Wiki 不存在")

    try:
        from app.modules.graph_rag import GraphRAGClient

        client = GraphRAGClient()
        index_result = await client.index_document(wiki.content, doc_id=wiki_id)

        return {
            "success": index_result["success"],
            "message": index_result["message"],
            "wiki_id": wiki_id,
        }

    except Exception as e:
        logger.error(f"Failed to index wiki to graph: {e}")
        raise HTTPException(status_code=500, detail=f"图谱索引失败: {str(e)}")


# ============================================================
# Wiki 空间
# ============================================================


@router.get("/spaces")
async def list_spaces(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取用户可见的空间列表"""
    # 用户自己的空间 + 组织空间 + 系统空间
    conditions = [WikiSpace.is_active]
    result = await db.execute(
        select(WikiSpace).where(*conditions).order_by(WikiSpace.type, WikiSpace.name)
    )
    spaces = result.scalars().all()
    return [
        {
            "id": s.id,
            "name": s.name,
            "description": s.description,
            "type": s.type,
            "owner_id": s.owner_id,
            "organization_id": s.organization_id,
            "doc_count": 0,
            "created_at": s.created_at.isoformat(),
        }
        for s in spaces
    ]


@router.post("/spaces/user/ensure")
async def ensure_user_space(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """确保用户有个人空间"""
    result = await db.execute(
        select(WikiSpace).where(
            WikiSpace.owner_id == current_user.id,
            WikiSpace.type == "user",
        )
    )
    space = result.scalar_one_or_none()
    if not space:
        space = WikiSpace(
            name=f"{current_user.display_name or current_user.username} 的空间",
            type="user",
            owner_id=current_user.id,
            organization_id=current_user.organization_id,
        )
        db.add(space)
        await db.commit()
        await db.refresh(space)
    return {"id": space.id, "name": space.name, "type": space.type}


@router.post("/spaces/org/ensure")
async def ensure_org_space(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """确保组织有共享空间"""
    if not current_user.is_org_admin and not current_user.is_super_admin:
        raise HTTPException(status_code=403, detail="需要管理员权限")
    org_id = current_user.organization_id
    if not org_id:
        raise HTTPException(status_code=400, detail="你未加入任何组织")
    result = await db.execute(
        select(WikiSpace).where(
            WikiSpace.organization_id == org_id,
            WikiSpace.type == "org",
        )
    )
    space = result.scalar_one_or_none()
    if not space:
        space = WikiSpace(
            name="组织共享空间",
            type="org",
            owner_id=current_user.id,
            organization_id=org_id,
        )
        db.add(space)
        await db.commit()
        await db.refresh(space)
    return {"id": space.id, "name": space.name, "type": space.type}


@router.get("/spaces/{space_id}")
async def get_space(
    space_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取空间详情"""
    space = await db.get(WikiSpace, space_id)
    if not space:
        raise HTTPException(status_code=404, detail="空间不存在")
    doc_count = (
        await db.execute(select(func.count(Wiki.id)).where(Wiki.space_id == space_id))
    ).scalar() or 0
    return {
        "id": space.id,
        "name": space.name,
        "description": space.description,
        "type": space.type,
        "owner_id": space.owner_id,
        "doc_count": doc_count,
        "created_at": space.created_at.isoformat(),
    }


@router.put("/spaces/{space_id}")
async def update_space(
    space_id: str,
    name: str | None = None,
    description: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """更新空间"""
    space = await db.get(WikiSpace, space_id)
    if not space:
        raise HTTPException(status_code=404, detail="空间不存在")
    if space.owner_id != current_user.id and not current_user.is_super_admin:
        raise HTTPException(status_code=403, detail="无权修改此空间")
    if name:
        space.name = name
    if description is not None:
        space.description = description
    await db.commit()
    return {"message": "更新成功"}


@router.get("/spaces/{space_id}/documents")
async def list_space_documents(
    space_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取空间内文档列表"""
    result = await db.execute(
        select(Wiki).where(Wiki.space_id == space_id).order_by(Wiki.updated_at.desc())
    )
    docs = result.scalars().all()
    return [
        {
            "id": d.id,
            "title": d.title,
            "path": d.path,
            "status": d.status,
            "version": d.version,
            "updated_at": d.updated_at.isoformat(),
        }
        for d in docs
    ]


# ============================================================
# 文档操作
# ============================================================


@router.get("/{wiki_id}/content")
async def get_wiki_content(
    wiki_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取文档纯文本内容"""
    wiki = await db.get(Wiki, wiki_id)
    if not wiki:
        raise HTTPException(status_code=404, detail="文档不存在")
    return {
        "id": wiki.id,
        "title": wiki.title,
        "content": wiki.content,
        "content_type": wiki.doc_type,
    }


@router.get("/{wiki_id}/outline")
async def get_wiki_outline(
    wiki_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """提取文档大纲（Markdown 标题）"""
    wiki = await db.get(Wiki, wiki_id)
    if not wiki:
        raise HTTPException(status_code=404, detail="文档不存在")
    headings = []
    for line in wiki.content.split("\n"):
        m = re.match(r"^(#{1,6})\s+(.+)$", line)
        if m:
            headings.append({"level": len(m.group(1)), "text": m.group(2).strip()})
    return {"id": wiki.id, "title": wiki.title, "outline": headings}


@router.get("/{wiki_id}/backlinks")
async def get_wiki_backlinks(
    wiki_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """查找引用此文档的其他文档"""
    wiki = await db.get(Wiki, wiki_id)
    if not wiki:
        raise HTTPException(status_code=404, detail="文档不存在")
    # 搜索内容中包含此文档路径的文档
    path_pattern = f"%{wiki.path}%"
    result = await db.execute(
        select(Wiki).where(Wiki.content.contains(path_pattern, autoescape=True))
    )
    backlinks = result.scalars().all()
    return {
        "id": wiki.id,
        "title": wiki.title,
        "backlinks": [
            {"id": b.id, "title": b.title, "path": b.path}
            for b in backlinks
            if b.id != wiki_id
        ],
    }


@router.get("/{wiki_id}/outlinks")
async def get_wiki_outlinks(
    wiki_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """提取文档中的外部链接"""
    wiki = await db.get(Wiki, wiki_id)
    if not wiki:
        raise HTTPException(status_code=404, detail="文档不存在")
    # 提取 Markdown 链接 [text](url)
    links = re.findall(r"\[([^\]]+)\]\(([^)]+)\)", wiki.content)
    return {
        "id": wiki.id,
        "title": wiki.title,
        "outlinks": [
            {"text": t, "url": u, "is_wiki": u.startswith("/wiki/")} for t, u in links
        ],
    }


@router.get("/{wiki_id}/download")
async def download_wiki(
    wiki_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """下载文档为 Markdown"""
    wiki = await db.get(Wiki, wiki_id)
    if not wiki:
        raise HTTPException(status_code=404, detail="文档不存在")
    return PlainTextResponse(
        wiki.content,
        media_type="text/markdown",
        headers={"Content-Disposition": f"attachment; filename={wiki.title}.md"},
    )


@router.post("/{wiki_id}/publish")
async def publish_wiki(
    wiki_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """发布文档（draft → published）"""
    if not current_user.is_org_admin and not current_user.is_super_admin:
        raise HTTPException(status_code=403, detail="需要管理员权限")
    wiki = await db.get(Wiki, wiki_id)
    if not wiki:
        raise HTTPException(status_code=404, detail="文档不存在")
    wiki.status = "published"
    await db.commit()
    return {"message": "已发布", "id": wiki.id, "status": wiki.status}


# ============================================================
# P1: 对话捕获 + 版本内容 + 审批
# ============================================================


@router.post("/capture_from_chat")
async def capture_from_chat(
    title: str,
    content: str,
    space_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """从对话内容创建 Wiki 文档"""
    # Auto-create user space if not specified
    if not space_id:
        sp_result = await db.execute(
            select(WikiSpace).where(
                WikiSpace.owner_id == current_user.id, WikiSpace.type == "user"
            )
        )
        space = sp_result.scalar_one_or_none()
        if not space:
            space = WikiSpace(
                name=f"{current_user.display_name or current_user.username} 的空间",
                type="user",
                owner_id=current_user.id,
                organization_id=current_user.organization_id,
            )
            db.add(space)
            await db.flush()
        space_id = space.id

    safe_title = re.sub(r"[^\w\-]", "-", title.lower())
    safe_path = f"/wiki/{current_user.username}/{safe_title}"
    wiki = Wiki(
        title=title,
        content=content,
        path=safe_path,
        space_id=space_id,
        created_by=current_user.id,
        scope="user",
        doc_type="markdown",
    )
    db.add(wiki)
    await db.commit()
    await db.refresh(wiki)
    return {"id": wiki.id, "title": wiki.title, "path": wiki.path, "space_id": space_id}


@router.get("/{wiki_id}/versions/{version}/content")
async def get_version_content(
    wiki_id: str,
    version: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取指定版本的内容"""
    result = await db.execute(
        select(WikiVersion).where(
            WikiVersion.wiki_id == wiki_id, WikiVersion.version == version
        )
    )
    ver = result.scalar_one_or_none()
    if not ver:
        raise HTTPException(status_code=404, detail="版本不存在")
    return {
        "wiki_id": wiki_id,
        "version": ver.version,
        "title": ver.title,
        "content": ver.content,
    }


@router.post("/{wiki_id}/submit-approval")
async def submit_wiki_approval(
    wiki_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """提交 Wiki 审批"""
    wiki = await db.get(Wiki, wiki_id)
    if not wiki:
        raise HTTPException(status_code=404, detail="文档不存在")
    approval = Approval(
        approval_type="task_approval",
        status="pending",
        title=f"Wiki审批: {wiki.title}",
        description=wiki.content[:200] if wiki.content else "",
        requester_id=current_user.id,
        resource_type="wiki",
        resource_id=wiki_id,
        target_scope="org",
    )
    wiki.status = "pending_approval"
    db.add(approval)
    await db.commit()
    await db.refresh(approval)
    return {"message": "已提交审批", "approval_id": approval.id}
