"""
PioneClaw 知识整合系统
从研究会话中提取知识点、生成解决方案文档

借鉴: AIE consolidator.py
"""

import json
import threading
import uuid
from datetime import datetime
from pathlib import Path

from loguru import logger

from .research import ResearchSession


class ConsolidationResult:
    """
    知识整合结果

    从研究会话中提取的知识文档
    """

    def __init__(
        self,
        session_id: str,
        problem_summary: str,
        solution_steps: list[str],
        pitfalls: list[str],
        new_knowledge: str,
        skill_name: str = None,
    ):
        self.id = str(uuid.uuid4())
        self.session_id = session_id
        self.problem_summary = problem_summary
        self.solution_steps = solution_steps
        self.pitfalls = pitfalls  # 遇到的陷阱/错误
        self.new_knowledge = new_knowledge
        self.skill_name = skill_name
        self.created_at = datetime.now().isoformat()

    def to_markdown(self) -> str:
        """转换为 Markdown 格式"""
        md = f"""---
title: {self.problem_summary}
type: solution
tags: [experience, solution]
source: research_session_{self.session_id}
created: {self.created_at}
---

# {self.problem_summary}

## 问题描述
{self.problem_summary}

## 解决步骤
"""

        for i, step in enumerate(self.solution_steps, 1):
            md += f"{i}. {step}\n"

        if self.pitfalls:
            md += "\n## Pitfalls\n"
            for pitfall in self.pitfalls:
                md += f"- {pitfall}\n"

        if self.new_knowledge:
            md += f"\n## 新增知识\n{self.new_knowledge}\n"

        md += f"\n---\n*来源: 研究会话 {self.session_id}*"
        return md

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "problem_summary": self.problem_summary,
            "solution_steps": self.solution_steps,
            "pitfalls": self.pitfalls,
            "new_knowledge": self.new_knowledge,
            "skill_name": self.skill_name,
            "created_at": self.created_at,
        }


class Consolidator:
    """
    知识整合器

    分析研究会话并提取：
    - 问题模式
    - 解决步骤
    - 遇到的陷阱
    - 引用的知识
    """

    def __init__(
        self,
        storage_dir: Path = None,
        solutions_dir: Path = None,
    ):
        self.storage_dir = storage_dir or Path("data/consolidation")
        self.solutions_dir = solutions_dir or self.storage_dir / "solutions"
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.solutions_dir.mkdir(parents=True, exist_ok=True)

        self._lock = threading.Lock()

    def consolidate_session(self, session: ResearchSession) -> ConsolidationResult:
        """
        整合会话为知识文档

        分析研究会话并提取：
        - 问题模式
        - 解决步骤
        - 遇到的陷阱
        - 引用的知识
        """
        # 1. 分析问题
        problem_summary = self._analyze_problem(session)

        # 2. 提取解决步骤
        solution_steps = self._extract_solution_steps(session)

        # 3. 提取陷阱
        pitfalls = self._extract_pitfalls(session)

        # 4. 提取新知识
        new_knowledge = self._extract_new_knowledge(session)

        # 5. 生成整合结果
        result = ConsolidationResult(
            session_id=session.id,
            problem_summary=problem_summary,
            solution_steps=solution_steps,
            pitfalls=pitfalls,
            new_knowledge=new_knowledge,
        )

        # 6. 保存文档
        self._save_solution(result)

        logger.info(f"Consolidated session {session.id}: {problem_summary}")
        return result

    def _analyze_problem(self, session: ResearchSession) -> str:
        """分析问题"""
        # 使用原始查询作为问题描述
        query = session.query

        # 如果有失败的经历，提取失败原因
        failed_attempts = [
            e
            for e in session.explorations
            if e.exploration_type == "result" and not e.metadata.get("success", True)
        ]

        if failed_attempts:
            reason = failed_attempts[0].content[:100]
            query = f"{query} (曾失败: {reason}...)"

        return query

    def _extract_solution_steps(self, session: ResearchSession) -> list[str]:
        """提取解决步骤"""
        steps = []

        # 从探索记录中提取
        for exp in session.explorations:
            if exp.exploration_type == "thinking":
                # 提取思考中的关键决策
                if exp.content and len(exp.content) > 20:
                    steps.append(f"分析: {exp.content[:200]}")
            elif exp.exploration_type == "action":
                # 记录执行的动作
                tool = exp.metadata.get("tool", "unknown")
                steps.append(f"执行: 使用 {tool} {exp.content[:150]}")
            elif exp.exploration_type == "result" and exp.metadata.get("success"):
                # 成功的尝试
                steps.append(f"成功: {exp.content[:200]}")

        return steps[:10]  # 最多 10 步

    def _extract_pitfalls(self, session: ResearchSession) -> list[str]:
        """提取遇到的陷阱"""
        pitfalls = []

        for exp in session.explorations:
            if exp.exploration_type == "result":
                success = exp.metadata.get("success", True)
                if not success:
                    # 记录失败
                    reason = exp.metadata.get("error", exp.content)
                    if reason:
                        pitfalls.append(
                            f"尝试 {exp.metadata.get('tool', '')}: {reason[:200]}"
                        )

        return pitfalls

    def _extract_new_knowledge(self, session: ResearchSession) -> str:
        """提取新增知识"""
        knowledge_parts = []

        # 检索的知识
        if session.retrieved_knowledge:
            sources = [
                k.get("source_name", "unknown") for k in session.retrieved_knowledge
            ]
            knowledge_parts.append(f"参考文档: {', '.join(set(sources))}")

        # 成功的关键
        if session.success and session.final_solution:
            key_points = session.final_solution[:500]
            knowledge_parts.append(f"最终方案: {key_points}")

        return "\n".join(knowledge_parts)

    def _save_solution(self, result: ConsolidationResult):
        """保存解决方案"""
        with self._lock:
            # 保存为 Markdown
            md_file = self.solutions_dir / f"{result.id}.md"
            md_file.write_text(result.to_markdown(), encoding="utf-8")

            # 保存为 JSON
            json_file = self.solutions_dir / f"{result.id}.json"
            json_file.write_text(
                json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    def get_solutions(self, limit: int = 20) -> list[dict]:
        """获取所有解决方案"""
        solutions = []

        for json_file in sorted(
            self.solutions_dir.glob("*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        ):
            try:
                data = json.loads(json_file.read_text(encoding="utf-8"))
                solutions.append(data)
                if len(solutions) >= limit:
                    break
            except Exception:
                pass

        return solutions

    def get_solution(self, solution_id: str) -> dict | None:
        """获取解决方案"""
        json_file = self.solutions_dir / f"{solution_id}.json"
        if json_file.exists():
            try:
                return json.loads(json_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        return None

    def get_solution_markdown(self, solution_id: str) -> str | None:
        """获取解决方案 Markdown"""
        md_file = self.solutions_dir / f"{solution_id}.md"
        if md_file.exists():
            return md_file.read_text(encoding="utf-8")
        return None

    def delete_solution(self, solution_id: str) -> bool:
        """删除解决方案"""
        with self._lock:
            json_file = self.solutions_dir / f"{solution_id}.json"
            md_file = self.solutions_dir / f"{solution_id}.md"

            deleted = False
            if json_file.exists():
                json_file.unlink()
                deleted = True
            if md_file.exists():
                md_file.unlink()
                deleted = True

            return deleted

    def count_solutions(self) -> int:
        """获取解决方案总数"""
        return len(list(self.solutions_dir.glob("*.json")))

    def get_stats(self) -> dict:
        """获取统计信息"""
        solutions = self.get_solutions(100)

        total = len(solutions)
        with_pitfalls = len([s for s in solutions if s.get("pitfalls")])

        return {
            "total_solutions": total,
            "with_pitfalls": with_pitfalls,
            "recent_count": len(self.get_solutions(10)),
        }


# 全局实例
_consolidator: Consolidator | None = None


def get_consolidator() -> Consolidator:
    global _consolidator
    if _consolidator is None:
        _consolidator = Consolidator()
    return _consolidator
