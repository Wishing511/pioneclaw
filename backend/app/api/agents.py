from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.auth import get_current_active_user
from app.core import get_db
from app.core.permissions import PermissionChecker
from app.models import Agent, AgentSkill, Skill, User
from app.schemas import (
    AgentCreate,
    AgentResponse,
    AgentUpdate,
    MessageResponse,
)

router = APIRouter(prefix="/agents", tags=["智能体管理"])


@router.get("", response_model=list[AgentResponse])
async def list_agents(
    skip: int = 0,
    limit: int = 20,
    status_filter: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取智能体列表"""
    query = select(Agent).options(
        selectinload(Agent.skills).selectinload(AgentSkill.skill)
    )

    if status_filter:
        query = query.where(Agent.status == status_filter)

    query = query.offset(skip).limit(limit).order_by(Agent.created_at.desc())
    result = await db.execute(query)
    agents = result.scalars().all()

    # 手动构造 skills 列表用于 Pydantic 序列化
    for agent in agents:
        agent.skills = [agent_skill.skill for agent_skill in agent.skills]

    return agents


@router.get("/{agent_id}", response_model=AgentResponse)
async def get_agent(
    agent_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取单个智能体详情"""
    result = await db.execute(
        select(Agent)
        .where(Agent.id == agent_id)
        .options(selectinload(Agent.skills).selectinload(AgentSkill.skill))
    )
    agent = result.scalar_one_or_none()

    if not agent:
        raise HTTPException(status_code=404, detail="智能体不存在")

    # 手动构造 skills 列表
    agent.skills = [agent_skill.skill for agent_skill in agent.skills]

    return agent


@router.post(
    "",
    response_model=AgentResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(PermissionChecker("agent:create"))],
)
async def create_agent(
    agent_data: AgentCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """创建智能体"""
    # 检查名称是否已存在
    result = await db.execute(select(Agent).where(Agent.name == agent_data.name))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="智能体名称已存在")

    # 创建智能体
    agent = Agent(
        name=agent_data.name,
        display_name=agent_data.display_name,
        description=agent_data.description,
        model=agent_data.model,
        max_turns=agent_data.max_turns,
        system_prompt=agent_data.system_prompt,
        creator_id=current_user.id,
    )
    db.add(agent)
    await db.flush()

    # 关联 skills
    if agent_data.skill_ids:
        for skill_id in agent_data.skill_ids:
            result = await db.execute(select(Skill).where(Skill.id == skill_id))
            skill = result.scalar_one_or_none()
            if skill:
                agent_skill = AgentSkill(agent_id=agent.id, skill_id=skill_id)
                db.add(agent_skill)

    await db.commit()

    # 重新加载 agent 和 skills
    result = await db.execute(
        select(Agent)
        .where(Agent.id == agent.id)
        .options(selectinload(Agent.skills).selectinload(AgentSkill.skill))
    )
    agent = result.scalar_one()
    agent.skills = [agent_skill.skill for agent_skill in agent.skills]

    return agent


@router.put(
    "/{agent_id}",
    response_model=AgentResponse,
    dependencies=[Depends(PermissionChecker("agent:update"))],
)
async def update_agent(
    agent_id: int,
    agent_data: AgentUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """更新智能体"""
    result = await db.execute(
        select(Agent)
        .where(Agent.id == agent_id)
        .options(selectinload(Agent.skills).selectinload(AgentSkill.skill))
    )
    agent = result.scalar_one_or_none()

    if not agent:
        raise HTTPException(status_code=404, detail="智能体不存在")

    # 更新字段
    update_data = agent_data.model_dump(exclude_unset=True, exclude={"skill_ids"})
    for key, value in update_data.items():
        setattr(agent, key, value)

    # 更新 skill 关联
    if agent_data.skill_ids is not None:
        # 删除旧关联
        old_skills = await db.execute(
            select(AgentSkill).where(AgentSkill.agent_id == agent.id)
        )
        for old_skill in old_skills.scalars().all():
            await db.delete(old_skill)

        # 添加新关联
        for skill_id in agent_data.skill_ids:
            agent_skill = AgentSkill(agent_id=agent.id, skill_id=skill_id)
            db.add(agent_skill)

    await db.commit()

    # 重新加载
    result = await db.execute(
        select(Agent)
        .where(Agent.id == agent.id)
        .options(selectinload(Agent.skills).selectinload(AgentSkill.skill))
    )
    agent = result.scalar_one()
    agent.skills = [agent_skill.skill for agent_skill in agent.skills]

    return agent


@router.delete(
    "/{agent_id}",
    response_model=MessageResponse,
    dependencies=[Depends(PermissionChecker("agent:delete"))],
)
async def delete_agent(
    agent_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """删除智能体"""
    result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = result.scalar_one_or_none()

    if not agent:
        raise HTTPException(status_code=404, detail="智能体不存在")

    await db.delete(agent)
    await db.commit()

    return MessageResponse(message="智能体已删除")
