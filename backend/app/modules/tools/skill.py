"""
SkillTool — Agent 通过工具调用按需激活技能

从 SkillsLoader 全局单例读取技能信息，支持：
- list: 列出所有可用技能及其描述
- activate: 加载指定技能的完整内容（去除 YAML frontmatter）
"""

import json
import logging

from app.modules.agent.skills import get_skills_loader
from app.modules.tools.base import BaseTool, ToolParameter

logger = logging.getLogger(__name__)


class SkillTool(BaseTool):
    """管理并激活技能 —— 查看可用技能列表或加载指定技能的完整内容"""

    name = "skill"
    description = (
        "管理并激活技能（skills）。技能是可复用的指令集，保存在 SKILL.md 文件中。\n"
        "支持两种操作：\n"
        "- list: 列出所有可用技能及其描述和来源\n"
        "- activate: 加载指定技能的完整内容到上下文中，供后续任务参考\n"
        "\n"
        "当用户要求使用某个技能、或任务提到技能名称时，应先用 'activate' 加载该技能的详细指令，"
        "然后按照技能内容执行。"
    )
    parameters = {
        "action": ToolParameter(
            type="string",
            description="操作类型：'list' 列出所有可用技能，'activate' 加载并激活指定技能",
            enum=["list", "activate"],
        ),
        "skill_name": ToolParameter(
            type="string",
            description="要激活的技能名称（action='activate' 时必填）",
            default="",
        ),
    }
    required = ["action"]

    async def execute(self, action: str, skill_name: str = "", **kwargs) -> str:
        try:
            loader = get_skills_loader()

            if action == "list":
                return self._handle_list(loader)

            elif action == "activate":
                return self._handle_activate(loader, skill_name)

            else:
                return json.dumps(
                    {
                        "success": False,
                        "error": f"未知操作: '{action}'。支持的操作: list, activate",
                    },
                    ensure_ascii=False,
                )

        except Exception as e:
            logger.error(f"SkillTool execution error: {e}")
            return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)

    def _handle_list(self, loader) -> str:
        """列出所有已启用且依赖满足的技能"""
        skills = []
        for name, skill in sorted(loader.skills.items()):
            if not skill.enabled:
                continue
            if not skill.check_requirements():
                continue
            skills.append(
                {
                    "name": name,
                    "description": skill.metadata.description or "",
                    "title": skill.metadata.title or name,
                    "source": skill.source,
                    "always": skill.metadata.always,
                    "tags": skill.metadata.tags,
                }
            )
        return json.dumps(
            {
                "success": True,
                "skills": skills,
                "total": len(skills),
            },
            ensure_ascii=False,
        )

    def _handle_activate(self, loader, skill_name: str) -> str:
        """激活指定技能，返回完整内容（不含 frontmatter）"""
        if not skill_name or not skill_name.strip():
            return json.dumps(
                {
                    "success": False,
                    "error": "activate 操作需要提供 skill_name 参数",
                },
                ensure_ascii=False,
            )

        skill_name = skill_name.strip()
        skill = loader.get_skill(skill_name)

        if not skill:
            available = sorted(loader.skills.keys())
            return json.dumps(
                {
                    "success": False,
                    "error": f"技能不存在: '{skill_name}'",
                    "available_skills": available,
                },
                ensure_ascii=False,
            )

        content = loader._strip_frontmatter(skill.content)

        return json.dumps(
            {
                "success": True,
                "name": skill.name,
                "title": skill.metadata.title or skill.name,
                "description": skill.metadata.description or "",
                "source": skill.source,
                "content": content,
            },
            ensure_ascii=False,
        )
