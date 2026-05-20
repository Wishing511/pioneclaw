"""
告警通知服务

当安全网关检测到高危拦截（BLOCK + critical）时，触发告警通知。
支持 Webhook 方式发送告警消息。
"""

import json
import logging
from typing import Dict, Any, Optional
from datetime import datetime, timezone

import httpx

from config import settings

logger = logging.getLogger(__name__)


class AlertService:
    """告警服务

    构建告警消息并通过 Webhook 发送。
    失败时记录日志，不影响主流程。
    """

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._client: Optional[httpx.AsyncClient] = None
        self._initialized = True

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(5.0))
        return self._client

    async def send_alert(
        self,
        check_point: str,
        result: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ):
        """发送高危拦截告警

        Args:
            check_point: 检查点 (input/output/tool)
            result: 安全检测结果
            context: 上下文信息 {user_id, username, session_id, agent_id, text}
        """
        if not settings.ALERT_ENABLED:
            return

        if not settings.ALERT_WEBHOOK_URL:
            logger.debug("ALERT_WEBHOOK_URL not configured, skip alert")
            return

        context = context or {}

        # 构建告警消息
        alert_message = self._build_alert_message(check_point, result, context)

        try:
            client = await self._get_client()
            resp = await client.post(
                settings.ALERT_WEBHOOK_URL,
                json=alert_message,
                headers={"Content-Type": "application/json"},
            )
            resp.raise_for_status()
            logger.info(f"Alert sent successfully: {alert_message['title']}")
        except Exception as e:
            logger.error(f"Failed to send alert: {e}")
            # 告警失败不影响主流程

    def _build_alert_message(
        self,
        check_point: str,
        result: Dict[str, Any],
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """构建告警消息体"""

        # 获取内容预览
        text = context.get("text", "")
        content_preview = text[:100] + "..." if len(text) > 100 else text

        # 获取匹配规则信息
        matched_rules = result.get("matched_rules", []) or []
        rule_names = []
        for rule in matched_rules:
            if isinstance(rule, dict):
                name = rule.get("type", "")
                word = rule.get("word", "")
                match = rule.get("match", "")
                if word:
                    name = f"{name}({word})"
                elif match:
                    name = f"{name}({match})"
                rule_names.append(name)

        # 检查点中文名
        checkpoint_labels = {
            "filter_input": "输入过滤",
            "filter_output": "输出过滤",
            "check_tool": "工具检查",
        }

        # 风险级别中文
        risk_labels = {
            "low": "低",
            "medium": "中",
            "high": "高",
            "critical": "严重",
        }

        # 操作中文
        action_labels = {
            "allow": "放行",
            "block": "拦截",
            "sanitize": "脱敏",
            "approve": "审批",
        }

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        return {
            "title": "安全网关高危拦截告警",
            "level": "critical",
            "timestamp": now,
            "fields": {
                "时间": now,
                "用户": context.get("username", "未知"),
                "用户ID": str(context.get("user_id", "-")),
                "检查点": checkpoint_labels.get(check_point, check_point),
                "风险级别": risk_labels.get(result.get("risk_level", "low"), "未知"),
                "操作": action_labels.get(result.get("action", "allow"), "未知"),
                "原因": result.get("reason", "-"),
                "内容预览": content_preview or "-",
                "匹配规则": ", ".join(rule_names) if rule_names else "-",
                "会话ID": context.get("session_id", "-"),
                "AgentID": context.get("agent_id", "-"),
                "TraceID": context.get("request_trace_id", "-"),
            },
            # Markdown 格式（供飞书/钉钉等展示）
            "markdown": self._build_markdown(
                now, context, check_point, result, content_preview, rule_names
            ),
        }

    @staticmethod
    def _build_markdown(
        now: str,
        context: Dict[str, Any],
        check_point: str,
        result: Dict[str, Any],
        content_preview: str,
        rule_names: list,
    ) -> str:
        """构建 Markdown 格式告警消息"""
        checkpoint_labels = {
            "filter_input": "输入过滤",
            "filter_output": "输出过滤",
            "check_tool": "工具检查",
        }
        risk_labels = {
            "low": "低",
            "medium": "中",
            "high": "高",
            "critical": "严重",
        }

        md = f"""## 安全网关高危拦截告警

- **时间**：{now}
- **用户**：{context.get("username", "未知")}
- **检查点**：{checkpoint_labels.get(check_point, check_point)}
- **风险级别**：{risk_labels.get(result.get("risk_level", "low"), "未知")}
- **操作**：拦截
- **原因**：{result.get("reason", "-")}
- **内容预览**：{content_preview or "-"}
- **匹配规则**：{", ".join(rule_names) if rule_names else "-"}
"""
        return md.strip()

    async def close(self):
        """关闭 HTTP 客户端"""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
