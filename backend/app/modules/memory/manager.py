"""Memory Manager — thread-safe singleton wrapper around MemoryManage.

Provides the global singleton pattern used by tools and API endpoints
to access the memory system without passing the manager through every layer.
"""

import logging
import threading
from typing import Callable, Optional, Tuple

import httpx

from .memory_manage import MemoryManage

logger = logging.getLogger(__name__)

_memory_manager: Optional[MemoryManage] = None
_lock = threading.Lock()


def get_current_memory_manager() -> Optional[MemoryManage]:
    """Get the thread-safe singleton MemoryManage instance."""
    return _memory_manager


def set_current_memory_manager(mm: Optional[MemoryManage]) -> None:
    """Set the thread-safe singleton MemoryManage instance."""
    global _memory_manager
    with _lock:
        _memory_manager = mm


def _build_default_llm_fns() -> Tuple[
    Optional[Callable[[str], str]],
    Optional[Callable[[str, str], str]],
]:
    """Query the default AIModelConfig from DB and build sync LLM closures.

    Returns (llm_query_fn, extract_agent_fn).  If no default config is found
    or the DB is unreachable, returns (None, None) so MemoryManage degrades
    gracefully to file-only mode.
    """
    try:
        from sqlalchemy import create_engine, select
        from sqlalchemy.orm import sessionmaker

        from app.core.config import settings
        from app.models.models import AIModelConfig
    except Exception as e:
        logger.warning("Unable to import DB deps for auto LLM config: %s", e)
        return None, None

    # Convert async DB URL to sync URL
    sync_url = settings.DATABASE_URL
    for prefix in ["+aiosqlite", "+asyncpg", "+asyncmy", "+aiomysql", "+aiopg"]:
        sync_url = sync_url.replace(prefix, "")

    try:
        engine = create_engine(sync_url, future=True, echo=False)
        Session = sessionmaker(bind=engine)
        with Session() as session:
            # 优先取默认配置
            config = session.execute(
                select(AIModelConfig).where(
                    AIModelConfig.is_default, AIModelConfig.is_active
                )
            ).scalar_one_or_none()
            # 没有默认配置时，取任意一个活跃配置
            if not config:
                config = session.execute(
                    select(AIModelConfig).where(AIModelConfig.is_active)
                ).scalars().first()
    except Exception as e:
        logger.warning("Failed to query AIModelConfig: %s", e)
        return None, None

    if not config:
        logger.info("No active AIModelConfig found; MemoryManage will run without LLM.")
        return None, None

    # Build sync LLM query closure
    model = config.model_name
    api_key = config.api_key
    api_base = config.base_url
    temperature = config.temperature
    max_tokens = config.max_tokens
    provider = config.provider

    def llm_query_fn(prompt: str) -> str:
        messages = [{"role": "user", "content": prompt}]

        if provider == "anthropic":
            url = api_base or "https://api.anthropic.com/v1/messages"
            headers = {
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            }
            body = {
                "model": model,
                "max_tokens": max_tokens,
                "messages": messages,
            }
        else:
            url = api_base or "https://api.openai.com/v1/chat/completions"
            if not url.endswith("/chat/completions"):
                url = url.rstrip("/") + "/chat/completions"
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }
            body = {
                "model": model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "messages": messages,
            }

        try:
            with httpx.Client(timeout=60.0) as client:
                response = client.post(url, headers=headers, json=body)
            if response.status_code != 200:
                logger.warning("LLM query failed: %s", response.text[:200])
                return ""

            data = response.json()
            if provider == "anthropic":
                return data.get("content", [{}])[0].get("text", "")
            message = data.get("choices", [{}])[0].get("message", {})
            return message.get("content", "") or ""
        except Exception as e:
            logger.warning("LLM query exception: %s", e)
            return ""

    def extract_agent_fn(system_prompt: str, user_prompt: str) -> str:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        if provider == "anthropic":
            url = api_base or "https://api.anthropic.com/v1/messages"
            headers = {
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            }
            body = {
                "model": model,
                "max_tokens": max_tokens,
                "messages": messages,
            }
            if messages and messages[0]["role"] == "system":
                body["system"] = messages.pop(0)["content"]
        else:
            url = api_base or "https://api.openai.com/v1/chat/completions"
            if not url.endswith("/chat/completions"):
                url = url.rstrip("/") + "/chat/completions"
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }
            body = {
                "model": model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "messages": messages,
            }

        try:
            with httpx.Client(timeout=120.0) as client:
                response = client.post(url, headers=headers, json=body)
            if response.status_code != 200:
                logger.warning("Extract agent failed: %s", response.text[:200])
                return ""

            data = response.json()
            if provider == "anthropic":
                return data.get("content", [{}])[0].get("text", "")
            message = data.get("choices", [{}])[0].get("message", {})
            return message.get("content", "") or ""
        except Exception as e:
            logger.warning("Extract agent exception: %s", e)
            return ""

    logger.info(
        "Auto-assembled LLM fns from default config: %s (%s)", model, provider
    )
    return llm_query_fn, extract_agent_fn


def create_memory_manager(
    memory_root: str,
    llm_query_fn: Optional[Callable[[str], str]] = None,
    extract_agent_fn: Optional[Callable[[str, str], str]] = None,
) -> MemoryManage:
    """Create, register and return the singleton MemoryManage instance.

    When *llm_query_fn* is not provided, the function automatically queries
    the default AIModelConfig from the database and builds synchronous LLM
    closures so that memory summarisation / ranking / deduplication work
    out of the box.
    """
    if llm_query_fn is None:
        # 优先复用已有单例的 LLM 配置，避免在异步上下文中重复查询 DB
        existing = get_current_memory_manager()
        if existing is not None:
            llm_query_fn = existing._llm_query
            extract_agent_fn = existing.extractor._run_agent
        else:
            llm_query_fn, extract_agent_fn = _build_default_llm_fns()

    mm = MemoryManage(
        memory_root=memory_root,
        llm_query_fn=llm_query_fn,
        extract_agent_fn=extract_agent_fn,
    )
    set_current_memory_manager(mm)
    return mm
