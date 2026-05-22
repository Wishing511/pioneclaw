"""
PioneClaw 研究会话系统
探索过程记录、知识检索、自动标记整合

借鉴: AIE research.py
"""

import contextlib
import json
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Literal

from loguru import logger


class Exploration:
    """
    探索记录

    记录研究会话中的每一步探索
    """

    def __init__(
        self,
        exploration_type: Literal[
            "thinking", "action", "result", "retrieved", "decision"
        ],
        content: str,
        metadata: dict = None,
    ):
        self.id = str(uuid.uuid4())
        self.exploration_type = exploration_type
        self.content = content
        self.metadata = metadata or {}
        self.timestamp = datetime.now().isoformat()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.exploration_type,
            "content": self.content,
            "metadata": self.metadata,
            "timestamp": self.timestamp,
        }


class ResearchSession:
    """
    研究会话

    记录一次完整的研究过程
    """

    def __init__(
        self,
        query: str,
        session_type: Literal["research", "chat"] = "chat",
    ):
        self.id = str(uuid.uuid4())
        self.query = query
        self.session_type = session_type
        self.start_time = datetime.now()
        self.end_time: datetime | None = None

        # 检索的知识
        self.retrieved_knowledge: list[dict] = []

        # 探索过程
        self.explorations: list[Exploration] = []

        # 最终结果
        self.final_solution: str = ""
        self.success: bool = False

        # 自动标记
        self.auto_tagged: bool = False
        self.need_consolidation: bool = False  # 需要知识整合

        # 统计
        self.tool_calls: int = 0
        self.successful_calls: int = 0

    def add_exploration(self, exploration: Exploration):
        """添加探索记录"""
        self.explorations.append(exploration)

        if exploration.exploration_type == "action":
            self.tool_calls += 1
        elif exploration.exploration_type == "result" and exploration.metadata.get(
            "success"
        ):
            self.successful_calls += 1

    def add_knowledge(self, knowledge_ref: dict):
        """添加检索的知识"""
        self.retrieved_knowledge.append(knowledge_ref)

    def complete(self, solution: str, success: bool):
        """完成会话"""
        self.end_time = datetime.now()
        self.final_solution = solution
        self.success = success

        # 自动标记
        self._auto_tag()

    def _auto_tag(self):
        """自动标记是否需要整合"""
        # 整合条件：
        # 1. 引用了企业知识
        # 2. 进行了探索尝试
        # 3. 成功解决了问题

        has_knowledge = len(self.retrieved_knowledge) > 0
        has_exploration = len(self.explorations) > 3  # 至少 3 次探索

        self.need_consolidation = has_knowledge and has_exploration and self.success
        self.auto_tagged = True

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "query": self.query,
            "session_type": self.session_type,
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "retrieved_knowledge": self.retrieved_knowledge,
            "explorations": [e.to_dict() for e in self.explorations],
            "final_solution": self.final_solution,
            "success": self.success,
            "auto_tagged": self.auto_tagged,
            "need_consolidation": self.need_consolidation,
            "tool_calls": self.tool_calls,
            "successful_calls": self.successful_calls,
        }

    @property
    def duration(self) -> float:
        """会话持续时间（秒）"""
        if self.end_time:
            return (self.end_time - self.start_time).total_seconds()
        return (datetime.now() - self.start_time).total_seconds()


class ResearchManager:
    """
    研究管理器

    管理研究会话的创建、查询、完成
    """

    def __init__(self, storage_dir: Path = None):
        self.storage_dir = storage_dir or Path("data/research")
        self.sessions_dir = self.storage_dir / "sessions"
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

        self._sessions: dict[str, ResearchSession] = {}
        self._lock = threading.Lock()

        self._load_recent_sessions()

    def _load_recent_sessions(self):
        """加载最近会话"""
        index_file = self.storage_dir / "index.json"
        if index_file.exists():
            try:
                data = json.loads(index_file.read_text(encoding="utf-8"))
                logger.info(f"Loaded {len(data.get('sessions', []))} research sessions")
            except Exception as e:
                logger.warning(f"Failed to load sessions index: {e}")

    def _save_session(self, session: ResearchSession):
        """保存会话"""
        file = self.sessions_dir / f"{session.id}.json"
        file.write_text(
            json.dumps(session.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # 更新索引
        self._update_index(session)

    def _update_index(self, session: ResearchSession):
        """更新索引"""
        with self._lock:
            index_file = self.storage_dir / "index.json"
            data = {"sessions": []}

            if index_file.exists():
                with contextlib.suppress(Exception):
                    data = json.loads(index_file.read_text(encoding="utf-8"))

            sessions = data.get("sessions", [])
            # 移除旧的
            sessions = [s for s in sessions if s.get("id") != session.id]
            # 添加新的
            sessions.insert(
                0,
                {
                    "id": session.id,
                    "query": session.query,
                    "start_time": session.start_time.isoformat(),
                    "end_time": session.end_time.isoformat()
                    if session.end_time
                    else None,
                    "success": session.success,
                    "need_consolidation": session.need_consolidation,
                    "auto_tagged": session.auto_tagged,
                },
            )

            # 只保留最近 1000 个
            data["sessions"] = sessions[:1000]

            index_file.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )

    def create_session(self, query: str, session_type: str = "chat") -> ResearchSession:
        """创建新会话"""
        session = ResearchSession(query=query, session_type=session_type)
        self._sessions[session.id] = session

        logger.info(f"Created research session: {session.id}")
        return session

    def get_session(self, session_id: str) -> ResearchSession | None:
        """获取会话"""
        # 先从内存查找
        if session_id in self._sessions:
            return self._sessions[session_id]

        # 从文件加载
        file = self.sessions_dir / f"{session_id}.json"
        if file.exists():
            try:
                data = json.loads(file.read_text(encoding="utf-8"))
                session = ResearchSession(
                    query=data["query"], session_type=data.get("session_type", "chat")
                )
                session.id = data["id"]
                session.start_time = datetime.fromisoformat(data["start_time"])
                session.retrieved_knowledge = data.get("retrieved_knowledge", [])

                # 恢复探索记录
                for exp_data in data.get("explorations", []):
                    exp = Exploration(
                        exploration_type=exp_data["type"],
                        content=exp_data["content"],
                        metadata=exp_data.get("metadata", {}),
                    )
                    exp.id = exp_data.get("id", exp.id)
                    exp.timestamp = exp_data.get("timestamp", exp.timestamp)
                    session.explorations.append(exp)

                session.final_solution = data.get("final_solution", "")
                session.success = data.get("success", False)
                session.auto_tagged = data.get("auto_tagged", False)
                session.need_consolidation = data.get("need_consolidation", False)
                session.tool_calls = data.get("tool_calls", 0)
                session.successful_calls = data.get("successful_calls", 0)

                if data.get("end_time"):
                    session.end_time = datetime.fromisoformat(data["end_time"])

                self._sessions[session_id] = session
                return session
            except Exception as e:
                logger.error(f"Failed to load session {session_id}: {e}")

        return None

    def add_exploration(
        self,
        session_id: str,
        exploration_type: str,
        content: str,
        metadata: dict = None,
    ) -> bool:
        """添加探索记录"""
        session = self.get_session(session_id)
        if not session:
            return False

        exploration = Exploration(exploration_type, content, metadata)
        session.add_exploration(exploration)

        # 实时保存
        self._save_session(session)

        return True

    def add_knowledge_ref(self, session_id: str, knowledge_ref: dict):
        """添加知识引用"""
        session = self.get_session(session_id)
        if session:
            session.add_knowledge(knowledge_ref)
            self._save_session(session)

    def complete_session(self, session_id: str, solution: str, success: bool):
        """完成会话"""
        session = self.get_session(session_id)
        if session:
            session.complete(solution, success)
            self._save_session(session)

            logger.info(
                f"Completed research session: {session_id}, "
                f"success={success}, need_consolidation={session.need_consolidation}"
            )

    def get_sessions_for_consolidation(self, limit: int = 10) -> list[ResearchSession]:
        """获取需要整合的会话"""
        sessions = []
        index_file = self.storage_dir / "index.json"

        if index_file.exists():
            try:
                data = json.loads(index_file.read_text(encoding="utf-8"))
                for item in data.get("sessions", []):
                    if item.get("need_consolidation"):
                        session = self.get_session(item["id"])
                        if session:
                            sessions.append(session)
                            if len(sessions) >= limit:
                                break
            except Exception as e:
                logger.error(f"Failed to get sessions for consolidation: {e}")

        return sessions

    def get_recent_sessions(self, limit: int = 20) -> list[dict]:
        """获取最近会话"""
        sessions = []
        index_file = self.storage_dir / "index.json"

        if index_file.exists():
            try:
                data = json.loads(index_file.read_text(encoding="utf-8"))
                for item in data.get("sessions", [])[:limit]:
                    sessions.append(item)
            except Exception:
                pass

        return sessions

    def get_stats(self) -> dict:
        """获取统计信息"""
        sessions = self.get_recent_sessions(100)

        total = len(sessions)
        successful = len([s for s in sessions if s.get("success")])
        need_consolidation = len([s for s in sessions if s.get("need_consolidation")])

        return {
            "total_sessions": total,
            "successful": successful,
            "need_consolidation": need_consolidation,
            "success_rate": round(successful / total if total > 0 else 0, 2),
        }


# 全局实例
_research: ResearchManager | None = None


def get_research_manager() -> ResearchManager:
    global _research
    if _research is None:
        _research = ResearchManager()
    return _research
