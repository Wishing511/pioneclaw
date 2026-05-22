from fastapi import APIRouter

from app.api.agent_execute import router as agent_execute_router
from app.api.agents import router as agents_router
from app.api.ai_configs import router as ai_configs_router
from app.api.approvals import router as approvals_router
from app.api.auth import router as auth_router
from app.api.channels import router as channels_router
from app.api.chat import router as chat_router
from app.api.concurrency_status import router as concurrency_router
from app.api.cron import router as cron_router
from app.api.dashboard import router as dashboard_router
from app.api.doctor import router as doctor_router
from app.api.files import router as files_router
from app.api.graph_rag import router as graph_rag_router
from app.api.interrupt import router as interrupt_router
from app.api.knowledge import router as knowledge_router
from app.api.layered_memory import router as layered_memory_router
from app.api.llm_usage import router as llm_usage_router
from app.api.logs import router as logs_router
from app.api.mcp import router as mcp_router
from app.api.memory_api import router as memory_api_router
from app.api.monitor import router as monitor_router
from app.api.organizations import router as organizations_router
from app.api.output import router as output_router

# PioneClaw 新增
from app.api.permissions import router as permissions_router
from app.api.personalities import router as personalities_router
from app.api.plugins import router as plugins_router
from app.api.providers import router as providers_router
from app.api.research import router as research_router
from app.api.roles import router as roles_router
from app.api.runner_releases import router as runner_releases_router
from app.api.runners import router as runners_router
from app.api.sessions import router as sessions_router
from app.api.settings import router as settings_router
from app.api.skill_eval import router as skill_eval_router
from app.api.skills import router as skills_router
from app.api.subagent import router as subagent_router
from app.api.task_board import router as task_board_router
from app.api.task_manager import router as task_manager_router
from app.api.taskflow import router as taskflow_router
from app.api.tasks import router as tasks_router
from app.api.tools_search import router as tools_search_router
from app.api.tracing import router as tracing_router
from app.api.users import router as users_router
from app.api.vector_store import router as vector_store_router
from app.api.websocket import router as websocket_router
from app.api.wiki import router as wiki_router
from app.api.workflow import router as workflow_router
from app.api.workspaces import router as workspaces_router

router = APIRouter()

router.include_router(auth_router)
router.include_router(agents_router)
router.include_router(skills_router)
router.include_router(dashboard_router)
router.include_router(runners_router)
router.include_router(ai_configs_router)
router.include_router(chat_router)
router.include_router(knowledge_router)
router.include_router(roles_router)
router.include_router(users_router)
router.include_router(tasks_router)
router.include_router(cron_router)
router.include_router(mcp_router)
router.include_router(settings_router)
router.include_router(logs_router)
router.include_router(agent_execute_router)
router.include_router(workflow_router)
router.include_router(subagent_router)
router.include_router(websocket_router)
router.include_router(channels_router)
router.include_router(providers_router)
router.include_router(task_manager_router)
router.include_router(personalities_router)
router.include_router(memory_api_router)
router.include_router(vector_store_router)
router.include_router(task_board_router)
router.include_router(research_router)
# PioneClaw 新增
router.include_router(permissions_router)
router.include_router(organizations_router)
router.include_router(wiki_router)
router.include_router(layered_memory_router)
router.include_router(graph_rag_router)
router.include_router(plugins_router)
router.include_router(workspaces_router)
router.include_router(approvals_router)
router.include_router(output_router)
router.include_router(taskflow_router)
router.include_router(interrupt_router)
router.include_router(tracing_router)
router.include_router(files_router)
router.include_router(skill_eval_router)
router.include_router(runner_releases_router)
router.include_router(sessions_router)
router.include_router(tools_search_router)
router.include_router(doctor_router)
router.include_router(monitor_router)
router.include_router(llm_usage_router)
router.include_router(concurrency_router)
