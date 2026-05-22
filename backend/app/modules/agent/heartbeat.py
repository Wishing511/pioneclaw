"""
Heartbeat - 主动问候系统

借鉴自 CountBot 的 heartbeat.py，实现主动问候功能。

功能：
1. 定时检查用户活跃状态
2. 在用户长时间未互动时主动问候
3. 根据性格预设生成问候语
4. 支持静默时段配置
5. 每日问候次数限制
"""

import json
import logging
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# 北京时区 UTC+8
SHANGHAI_TZ = timezone(timedelta(hours=8))

# 内置 heartbeat cron job 的固定 ID
HEARTBEAT_JOB_ID = "builtin:heartbeat"
HEARTBEAT_JOB_NAME = "系统问候（内置）"
HEARTBEAT_SCHEDULE = "0 * * * *"  # 每小时整点检查
HEARTBEAT_MESSAGE = "__heartbeat__"  # 特殊标记，executor 识别后交给 HeartbeatService

# 默认配置
DEFAULT_IDLE_THRESHOLD_HOURS = 4
DEFAULT_ACTIVE_START = 8  # 北京时间
DEFAULT_ACTIVE_END = 22  # 北京时间
DEFAULT_MAX_GREETS_PER_DAY = 2  # 每天最多问候次数


@dataclass(frozen=True)
class HeartbeatDispatch:
    """一次已通过前置检查、等待真正投递的问候任务"""

    session_id: str
    channel: str
    account_id: str
    chat_id: str
    today: str
    matched_time: int
    idle_hours: float
    greet_num: int
    greeting: str


@dataclass
class HeartbeatConfig:
    """Heartbeat 配置"""

    enabled: bool = False
    idle_threshold_hours: int = DEFAULT_IDLE_THRESHOLD_HOURS
    quiet_start: int = 21  # 静默开始时间（北京时间）
    quiet_end: int = 8  # 静默结束时间
    max_greets_per_day: int = DEFAULT_MAX_GREETS_PER_DAY
    channel: str | None = None
    account_id: str | None = None
    chat_id: str | None = None
    ai_name: str = "小助手"
    user_name: str = "用户"
    personality: str = "professional"
    custom_personality: str = ""
    schedule: str = HEARTBEAT_SCHEDULE


class HeartbeatService:
    """
    主动问候服务

    根据判断是否触发以及如何投递主动问候。
    """

    def __init__(
        self,
        config: HeartbeatConfig,
        workspace: Path,
        llm_client=None,
        memory_store=None,
        db_session_factory=None,
    ):
        self.config = config
        self.workspace = workspace
        self.llm_client = llm_client
        self.memory_store = memory_store
        self.db_session_factory = db_session_factory

        # 状态文件
        self._state_file = workspace / "memory" / "heartbeat_state.json"

        logger.debug(
            f"HeartbeatService initialized: idle>{config.idle_threshold_hours}h, "
            f"quiet {config.quiet_start}:00-{config.quiet_end}:00, "
            f"max {config.max_greets_per_day} greets/day"
        )

    @staticmethod
    def _now_shanghai() -> datetime:
        """获取当前北京时间"""
        return datetime.now(SHANGHAI_TZ)

    def _is_quiet_hour(self, hour: int) -> bool:
        """
        判断当前小时是否在静默时段

        支持跨越午夜时段，如 quiet_start=22, quiet_end=8 表示 22:00-08:00 静默。
        """
        if self.config.quiet_start <= self.config.quiet_end:
            # 不跨越午夜，如 1:00-6:00
            return self.config.quiet_start <= hour < self.config.quiet_end
        else:
            # 跨越午夜，如 22:00-8:00
            return hour >= self.config.quiet_start or hour < self.config.quiet_end

    def _load_state(self) -> dict:
        """从文件加载状态"""
        try:
            if self._state_file.exists():
                with open(self._state_file, encoding="utf-8") as f:
                    return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load heartbeat state: {e}")
        return {}

    def _save_state(self, state: dict) -> None:
        """保存状态到文件"""
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self._state_file, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Failed to save heartbeat state: {e}")

    def _build_config_signature(self) -> str:
        """生成配置签名"""
        return f"{self.config.quiet_start}:{self.config.quiet_end}:{self.config.max_greets_per_day}"

    def _prune_old_state(self, state: dict) -> None:
        """清理旧数据（保留最近7天）"""
        dates = sorted(state.keys())
        if len(dates) > 7:
            for old_date in dates[:-7]:
                del state[old_date]

    def _generate_random_times(self, date: str) -> list[int]:
        """
        为指定日期生成随机问候时间点

        Returns:
            List[int]: 分钟数列表，如 [615, 780] 表示 10:15, 13:00
        """
        try:
            date_obj = datetime.strptime(date, "%Y-%m-%d")
            beijing_midnight = date_obj.replace(tzinfo=SHANGHAI_TZ)
            seed = int(beijing_midnight.timestamp())
        except Exception:
            seed = 0
        rng = random.Random(seed)

        # 计算活跃时段
        qs = self.config.quiet_start
        qe = self.config.quiet_end
        total = 24 * 60

        if qs > qe:
            # 跨越午夜静默，如 21:00-08:00，活跃时段 [qe*60, qs*60)
            active_segments = [(qe * 60, qs * 60)]
        elif qs < qe:
            # 不跨越午夜静默，如 01:00-06:00，活跃时段 [0, qs*60) 和 [qe*60, 24*60)
            active_segments = []
            if qs * 60 > 0:
                active_segments.append((0, qs * 60))
            if qe * 60 < total:
                active_segments.append((qe * 60, total))
        else:
            # 无静默时段，全天活跃
            active_segments = [(0, total)]

        # 计算总活跃分钟数
        active_total = sum(end - start for start, end in active_segments)
        if active_total <= 0 or self.config.max_greets_per_day <= 0:
            return []

        # 分段均匀分布问候时间
        segment_size = active_total // self.config.max_greets_per_day
        if segment_size < 1:
            segment_size = 1

        def virtual_to_real(v: int) -> int:
            """将虚拟分钟偏移映射到真实分钟"""
            for seg_start, seg_end in active_segments:
                seg_len = seg_end - seg_start
                if v < seg_len:
                    return seg_start + v
                v -= seg_len
            last_start, last_end = active_segments[-1]
            return last_end - 1

        times = []
        for i in range(self.config.max_greets_per_day):
            v_start = i * segment_size
            v_end = v_start + segment_size
            if v_end > active_total:
                v_end = active_total
            v = rng.randint(v_start, v_end - 1)
            times.append(virtual_to_real(v))

        return sorted(times)

    def _build_today_entry(
        self, today: str, previous_entry: dict | None = None
    ) -> dict:
        """构建当日状态条目"""
        previous_entry = previous_entry if isinstance(previous_entry, dict) else {}
        greeted_times = list(previous_entry.get("greeted_times") or [])
        return {
            "scheduled_times": self._generate_random_times(today),
            "greeted_times": greeted_times,
            "count": len(greeted_times),
            "config_signature": self._build_config_signature(),
        }

    def _get_today_state(self, today: str) -> dict:
        """获取当日状态"""
        state = self._load_state()
        today_entry = state.get(today)
        expected_signature = self._build_config_signature()

        needs_rebuild = (
            not isinstance(today_entry, dict)
            or today_entry.get("config_signature") != expected_signature
        )

        if needs_rebuild:
            state[today] = self._build_today_entry(today, today_entry)
            self._prune_old_state(state)
            self._save_state(state)
            logger.info(
                f"已生成今日随机问候时间 | 日期={today} | "
                f"时间点={[f'{t // 60}:{t % 60:02d}' for t in state[today]['scheduled_times']]}"
            )

        return state[today]

    def _mark_greeted(self, today: str, scheduled_time: int) -> None:
        """标记某计划时间点已问候"""
        state = self._load_state()

        if today not in state or not isinstance(state[today], dict):
            state[today] = self._build_today_entry(today)

        if scheduled_time not in state[today]["greeted_times"]:
            state[today]["greeted_times"].append(scheduled_time)
            state[today]["count"] = len(state[today]["greeted_times"])

        state[today]["config_signature"] = self._build_config_signature()
        self._prune_old_state(state)
        self._save_state(state)

    def _should_greet_now(self, today: str, current_minute: int) -> int | None:
        """
        判断当前时间是否应该问候

        Returns:
            匹配的计划时间点（分钟数），或 None
        """
        today_state = self._get_today_state(today)
        scheduled_times = today_state["scheduled_times"]
        greeted_times = today_state["greeted_times"]

        # 检查是否已达到每日上限
        if today_state["count"] >= self.config.max_greets_per_day:
            return None

        # 找到当前时间附近且未问候的计划时间点
        for scheduled_time in scheduled_times:
            if scheduled_time in greeted_times:
                continue
            if abs(current_minute - scheduled_time) <= 30:  # 30分钟窗口
                return scheduled_time

        return None

    async def prepare_dispatch(
        self,
        session_id: str,
        channel: str,
        account_id: str,
        chat_id: str,
    ) -> HeartbeatDispatch | None:
        """
        准备一次待投递的问候任务

        流程：
        1. 静默时段检查
        2. 计划时间检查
        3. 次数检查
        4. 用户空闲检查
        5. LLM 生成问候语
        """
        now = self._now_shanghai()
        today = now.strftime("%Y-%m-%d")
        current_minute = now.hour * 60 + now.minute

        # 1. 静默时段检查
        if self._is_quiet_hour(now.hour):
            logger.debug(f"Heartbeat skipped: {now.hour}:00 is in quiet hours")
            return None

        # 2. 计划时间检查
        matched_time = self._should_greet_now(today, current_minute)
        if matched_time is None:
            logger.debug("Heartbeat skipped: not in scheduled time window")
            return None

        # 3. 次数检查
        today_state = self._get_today_state(today)
        greet_num = today_state["count"] + 1

        # 4. 用户空闲检查
        idle_hours = await self._get_user_idle_hours(session_id)
        if idle_hours is None or idle_hours < self.config.idle_threshold_hours:
            logger.debug(
                f"Heartbeat skipped: idle {idle_hours}h < threshold {self.config.idle_threshold_hours}h"
            )
            return None

        # 5. 生成问候语
        greeting = await self._generate_greeting(now, idle_hours)
        if not greeting:
            return None

        logger.info(
            f"准备问候任务 | 会话={session_id} | "
            f"空闲={idle_hours:.1f}h | 第{greet_num}/{self.config.max_greets_per_day}次"
        )

        return HeartbeatDispatch(
            session_id=session_id,
            channel=channel,
            account_id=account_id,
            chat_id=chat_id,
            today=today,
            matched_time=matched_time,
            idle_hours=idle_hours,
            greet_num=greet_num,
            greeting=greeting,
        )

    def commit_dispatch(self, dispatch: HeartbeatDispatch) -> None:
        """确认投递成功，提交问候记录"""
        self._mark_greeted(dispatch.today, dispatch.matched_time)
        logger.info(
            f"问候已成功发送 | 第{dispatch.greet_num}/{self.config.max_greets_per_day}次 | "
            f"内容={dispatch.greeting[:80]}"
        )

    async def _get_user_idle_hours(self, session_id: str) -> float | None:
        """查询目标会话用户最后一次消息时间，返回空闲小时数"""
        if not self.db_session_factory:
            return None

        try:
            # 这里需要根据实际的数据库模型调整
            # 假设有 Message 表
            async with self.db_session_factory():
                # 查询最后一条用户消息时间
                # result = await db.execute(...)
                # last_msg_time = result.scalar()
                # return (now - last_msg_time).total_seconds() / 3600
                pass
        except Exception as e:
            logger.error(f"Failed to get user idle hours: {e}")

        return None

    async def _generate_greeting(self, now: datetime, idle_hours: float) -> str:
        """用 LLM 生成问候语"""
        if not self.llm_client:
            # 无 LLM，返回默认问候
            return self._get_default_greeting(now, idle_hours)

        from app.modules.agent.personalities import get_personality_prompt
        from app.modules.agent.prompts import get_heartbeat_greeting_prompt

        hour = now.hour
        if hour < 12:
            time_desc = f"上午{hour}点"
        elif hour < 14:
            time_desc = f"中午{hour}点"
        elif hour < 18:
            time_desc = f"下午{hour}点"
        else:
            time_desc = f"晚上{hour}点"

        # 获取记忆上下文
        memory_context = ""
        if self.memory_store:
            try:
                recent = self.memory_store.get_recent(5)
                if recent and "记忆为空" not in recent:
                    memory_context = f"近期记忆（可参考但不必提及）:\n{recent}"
            except Exception:
                pass

        # 获取性格描述
        personality_desc = get_personality_prompt(
            self.config.personality,
            self.config.custom_personality,
        )

        prompt = get_heartbeat_greeting_prompt(
            ai_name=self.config.ai_name,
            time_desc=time_desc,
            user_name=self.config.user_name,
            idle_hours=idle_hours,
            personality_desc=personality_desc,
            memory_context=memory_context,
        )

        try:
            # 调用 LLM
            if hasattr(self.llm_client, "chat"):
                response = await self.llm_client.chat(
                    [{"role": "user", "content": prompt}],
                    temperature=0.8,
                    max_tokens=100,
                )
                greeting = response.get("content", "").strip()
            else:
                greeting = ""

            # 过滤异常输出
            if not greeting or len(greeting) > 50:
                return self._get_default_greeting(now, idle_hours)

            return greeting

        except Exception as e:
            logger.error(f"Failed to generate greeting: {e}")
            return self._get_default_greeting(now, idle_hours)

    def _get_default_greeting(self, now: datetime, idle_hours: float) -> str:
        """获取默认问候语"""
        hour = now.hour
        if hour < 12:
            return "早上好！好久没见了，有什么需要帮忙的吗？"
        elif hour < 18:
            return "下午好！看到你回来了，有什么想聊的吗？"
        else:
            return "晚上好！今天过得怎么样？"

    def refresh_today_schedule(self, reason: str = "") -> bool:
        """刷新今日问候计划"""
        today = self._now_shanghai().strftime("%Y-%m-%d")
        state = self._load_state()
        previous_entry = state.get(today)

        if not isinstance(previous_entry, dict):
            return False

        state[today] = self._build_today_entry(today, previous_entry)
        self._prune_old_state(state)
        self._save_state(state)

        logger.info(f"已刷新今日问候计划 | 原因={reason}")
        return True


# ==================== 便捷函数 ====================


def create_heartbeat_service(
    config: HeartbeatConfig,
    workspace: Path,
    llm_client=None,
    memory_store=None,
) -> HeartbeatService:
    """创建 Heartbeat 服务实例"""
    return HeartbeatService(
        config=config,
        workspace=workspace,
        llm_client=llm_client,
        memory_store=memory_store,
    )


def get_default_heartbeat_config() -> HeartbeatConfig:
    """获取默认配置"""
    return HeartbeatConfig()
