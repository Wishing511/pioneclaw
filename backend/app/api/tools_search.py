"""
工具搜索 + Git 上下文 API
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.auth import get_current_active_user
from app.models import User
from app.modules.agent.git_context import format_git_prompt, get_git_context
from app.modules.tools.registry import ToolRegistry

router = APIRouter(tags=["工具搜索 & Git"])


class ToolSearchItem(BaseModel):
    name: str
    description: str
    parameters: dict | None = None


@router.get("/tools/search", response_model=list[ToolSearchItem])
async def search_tools(
    query: str = "",
    limit: int = 10,
    current_user: User = Depends(get_current_active_user),
):
    """搜索可用工具（按名称/描述模糊匹配）"""
    from app.modules.tools import register_builtin_tools

    registry = ToolRegistry()
    register_builtin_tools(registry)

    results = []
    q = query.lower()
    for name in registry.list_tools():
        if not q or q in name.lower():
            tool = registry.get_tool(name)
            desc = (tool.description or "").lower() if tool else ""
            if q and q not in name.lower() and q not in desc:
                continue
            results.append(
                ToolSearchItem(
                    name=tool.name if tool else name,
                    description=tool.description if tool else "",
                    parameters={
                        k: v.model_dump() if hasattr(v, "model_dump") else str(v)
                        for k, v in (tool.parameters.items() if tool else {})
                    }
                    if tool
                    else None,
                )
            )

    return results[:limit]


@router.get("/git/context")
async def get_git_prompt(
    current_user: User = Depends(get_current_active_user),
):
    """获取当前工作目录的 Git 上下文"""
    ctx = get_git_context()
    return {
        "has_repo": ctx.has_repo,
        "branch": ctx.branch,
        "last_commit": ctx.last_commit,
        "last_commit_msg": ctx.last_commit_msg,
        "unstaged": ctx.unstaged,
        "staged": ctx.staged,
        "recent_commits": ctx.recent_commits,
        "prompt_text": format_git_prompt(ctx),
    }
