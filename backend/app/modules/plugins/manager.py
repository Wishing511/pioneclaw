"""
PluginManager - 插件管理器

支持：
- 插件发现（从指定目录加载 Python 模块）
- 生命周期钩子 (on_load / on_unload / on_error)
- 热加载/卸载
- 事件总线集成
- 插件依赖检查
- 插件配置
"""

import asyncio
import contextlib
import importlib
import importlib.util
import inspect
import logging
import os
import sys
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .event_bus import EventBus
from .lifecycle import PluginLifecycle

logger = logging.getLogger(__name__)


class PluginState(Enum):
    """插件状态（扩展版）

    原有：UNLOADED, LOADING, LOADED, ERROR, UNLOADING
    新增：RETRYING, PAUSED, STOPPING, STOPPED, DISABLED
    """

    UNLOADED = "unloaded"
    LOADING = "loading"
    LOADED = "loaded"  # 加载成功（= active / running）
    ERROR = "error"
    UNLOADING = "unloading"
    RETRYING = "retrying"  # 错误后自动重试中
    PAUSED = "paused"  # 已暂停（事件订阅挂起）
    STOPPING = "stopping"  # 正在停止
    STOPPED = "stopped"  # 已停止
    DISABLED = "disabled"  # 已禁用（保留元数据）


@dataclass
class PluginInfo:
    """插件信息"""

    plugin_id: str
    name: str
    version: str = "0.1.0"
    description: str = ""
    author: str = ""
    dependencies: list[str] = field(default_factory=list)  # 依赖的其他插件 ID
    config_schema: dict | None = None  # 配置 schema
    state: PluginState = PluginState.UNLOADED
    module: Any | None = None  # 加载后的 Python 模块
    error: str | None = None
    config: dict[str, Any] = field(default_factory=dict)
    subscriptions: list[str] = field(default_factory=list)  # 订阅 ID 列表
    # 生命周期 (set after __init__ by PluginManager)
    lifecycle: PluginLifecycle | None = None


# 插件模块需实现的钩子函数名
PLUGIN_HOOKS = {
    "on_load",
    "on_unload",
    "on_error",
    "on_event",
}


class PluginManager:
    """
    插件管理器

    用法:
        bus = EventBus()
        manager = PluginManager(event_bus=bus, plugin_dir="plugins")

        # 加载插件
        info = manager.load_plugin("my_plugin")

        # 热重载
        manager.reload_plugin("my_plugin")

        # 卸载
        manager.unload_plugin("my_plugin")
    """

    def __init__(
        self,
        event_bus: EventBus | None = None,
        plugin_dir: str | None = None,
    ) -> None:
        """
        初始化 PluginManager

        Args:
            event_bus: 事件总线实例
            plugin_dir: 插件目录路径
        """
        self._event_bus = event_bus or EventBus()
        self._plugin_dir = plugin_dir
        self._plugins: dict[str, PluginInfo] = {}
        self._health_monitor_task: asyncio.Task | None = None
        self._retry_worker_task: asyncio.Task | None = None

    @property
    def event_bus(self) -> EventBus:
        """获取事件总线"""
        return self._event_bus

    def discover_plugins(self) -> list[str]:
        """
        发现插件目录中的所有插件

        Returns:
            List[str]: 插件 ID 列表
        """
        if not self._plugin_dir or not os.path.isdir(self._plugin_dir):
            return []

        plugin_ids = []
        for entry in os.listdir(self._plugin_dir):
            entry_path = os.path.join(self._plugin_dir, entry)
            # Python 包（含 __init__.py 的目录）
            if os.path.isdir(entry_path) and os.path.isfile(
                os.path.join(entry_path, "__init__.py")
            ):
                plugin_ids.append(entry)
            # 单文件 .py 模块
            elif entry.endswith(".py") and not entry.startswith("_"):
                plugin_ids.append(entry[:-3])

        return sorted(plugin_ids)

    def load_plugin(self, plugin_id: str, config: dict | None = None) -> PluginInfo:
        """
        加载插件

        Args:
            plugin_id: 插件 ID
            config: 插件配置

        Returns:
            PluginInfo: 插件信息

        Raises:
            ValueError: 插件未找到或依赖未满足
        """
        if plugin_id in self._plugins and self._plugins[plugin_id].state in (
            PluginState.LOADED,
            PluginState.LOADING,
        ):
            logger.warning(f"[PluginManager] 插件 '{plugin_id}' 已加载/加载中")
            return self._plugins[plugin_id]

        info = PluginInfo(plugin_id=plugin_id, name=plugin_id)
        if config:
            info.config = config

        # 初始化生命周期管理器
        if info.lifecycle is None:
            info.lifecycle = PluginLifecycle(plugin_id=plugin_id)
        info.lifecycle.transition("loading", reason="load_plugin called")
        info.state = PluginState.LOADING

        self._plugins[plugin_id] = info

        try:
            # 加载模块
            module = self._import_plugin(plugin_id)
            if module is None:
                raise ValueError(f"插件 '{plugin_id}' 未找到")

            info.module = module

            # 读取元数据
            info.name = getattr(module, "__plugin_name__", plugin_id)
            info.version = getattr(module, "__plugin_version__", "0.1.0")
            info.description = getattr(module, "__plugin_description__", "")
            info.author = getattr(module, "__plugin_author__", "")
            info.dependencies = getattr(module, "__plugin_dependencies__", [])
            info.config_schema = getattr(module, "__plugin_config_schema__", None)

            # 设置健康检查函数（插件可选实现 health_check 函数）
            health_fn = getattr(module, "health_check", None)
            if callable(health_fn):
                info.lifecycle.set_health_check_fn(health_fn)

            # 检查依赖
            missing = self._check_dependencies(info)
            if missing:
                raise ValueError(f"插件 '{plugin_id}' 依赖未满足: {', '.join(missing)}")

            # 注册事件处理器
            self._register_event_handlers(info)

            # 调用 on_load
            on_load = getattr(module, "on_load", None)
            if on_load and callable(on_load):
                result = on_load(self._event_bus, info.config)
                if inspect.isawaitable(result):
                    logger.info(
                        f"[PluginManager] 插件 '{plugin_id}' on_load 是异步函数，需在事件循环中调用"
                    )

            info.state = PluginState.LOADED
            info.lifecycle.transition("loaded", reason="load succeeded")
            info.lifecycle.reset_retry()
            info.error = None
            logger.info(f"[PluginManager] 插件 '{plugin_id}' v{info.version} 加载成功")

        except Exception as exc:
            info.state = PluginState.ERROR
            info.error = str(exc)

            try:
                info.lifecycle.transition("error", reason=str(exc))
            except ValueError:
                pass  # 可能已在 error 状态

            logger.error(f"[PluginManager] 插件 '{plugin_id}' 加载失败: {exc}")

            # 调用 on_error
            if info.module:
                on_error = getattr(info.module, "on_error", None)
                if on_error and callable(on_error):
                    with contextlib.suppress(Exception):
                        on_error(exc)

            # 自动进入重试
            if info.lifecycle.should_auto_restart:
                info.state = PluginState.RETRYING
                info.lifecycle.retry_count += 1
                with contextlib.suppress(ValueError):
                    info.lifecycle.transition(
                        "retrying",
                        reason=f"auto-retry {info.lifecycle.retry_count}/{info.lifecycle.max_retries}",
                    )
                logger.info(
                    f"[PluginManager] 插件 '{plugin_id}' 将在 "
                    f"{info.lifecycle.compute_retry_delay_ms()}ms 后自动重试 "
                    f"({info.lifecycle.retry_count}/{info.lifecycle.max_retries})"
                )
                self._ensure_retry_worker()

        return info

    async def load_plugin_async(
        self, plugin_id: str, config: dict | None = None
    ) -> PluginInfo:
        """异步加载插件（支持异步 on_load）"""
        info = self.load_plugin(plugin_id, config)

        if info.state == PluginState.LOADED and info.module:
            on_load = getattr(info.module, "on_load", None)
            if on_load and callable(on_load):
                result = on_load(self._event_bus, info.config)
                if inspect.isawaitable(result):
                    try:
                        await result
                    except Exception as exc:
                        info.state = PluginState.ERROR
                        info.error = str(exc)
                        logger.error(
                            f"[PluginManager] 插件 '{plugin_id}' on_load 失败: {exc}"
                        )

        return info

    def unload_plugin(self, plugin_id: str) -> bool:
        """
        卸载插件（支持从 LOADED/PAUSED/ERROR/STOPPED/DISABLED 状态卸载）

        Args:
            plugin_id: 插件 ID

        Returns:
            bool: 是否成功卸载
        """
        if plugin_id not in self._plugins:
            logger.warning(f"[PluginManager] 插件 '{plugin_id}' 未加载")
            return False

        info = self._plugins[plugin_id]
        unloadable = {
            PluginState.LOADED,
            PluginState.PAUSED,
            PluginState.ERROR,
            PluginState.STOPPED,
            PluginState.DISABLED,
            PluginState.RETRYING,
        }
        if info.state not in unloadable:
            logger.warning(
                f"[PluginManager] 插件 '{plugin_id}' 状态为 {info.state}，无法卸载"
            )
            return False

        info.state = PluginState.UNLOADING
        if info.lifecycle:
            with contextlib.suppress(ValueError):
                info.lifecycle.transition("unloading", reason="unload_plugin called")

        try:
            # 取消事件订阅
            for sub_id in info.subscriptions:
                self._event_bus.unsubscribe(sub_id)
            info.subscriptions.clear()

            # 调用 on_unload
            if info.module:
                on_unload = getattr(info.module, "on_unload", None)
                if on_unload and callable(on_unload):
                    on_unload()

            # 从 sys.modules 移除
            if info.module and hasattr(info.module, "__name__"):
                module_name = info.module.__name__
                if module_name in sys.modules:
                    del sys.modules[module_name]

            info.state = PluginState.UNLOADED
            info.module = None
            info.error = None
            if info.lifecycle:
                info.lifecycle.transition("unloaded", reason="unload succeeded")
            logger.info(f"[PluginManager] 插件 '{plugin_id}' 已卸载")

        except Exception as exc:
            info.state = PluginState.ERROR
            info.error = str(exc)
            if info.lifecycle:
                info.lifecycle.record_error(f"unload failed: {exc}")
            logger.error(f"[PluginManager] 插件 '{plugin_id}' 卸载失败: {exc}")
            return False

        return True

    def reload_plugin(self, plugin_id: str) -> PluginInfo:
        """
        热重载插件

        Args:
            plugin_id: 插件 ID

        Returns:
            PluginInfo: 重载后的插件信息
        """
        config = None
        if plugin_id in self._plugins:
            config = self._plugins[plugin_id].config

        self.unload_plugin(plugin_id)

        # 强制重新导入
        if plugin_id in self._plugins:
            del self._plugins[plugin_id]

        return self.load_plugin(plugin_id, config=config)

    async def reload_plugin_async(self, plugin_id: str) -> PluginInfo:
        """异步热重载插件"""
        config = None
        if plugin_id in self._plugins:
            config = self._plugins[plugin_id].config

        self.unload_plugin(plugin_id)

        if plugin_id in self._plugins:
            del self._plugins[plugin_id]

        return await self.load_plugin_async(plugin_id, config=config)

    def load_all(self) -> list[PluginInfo]:
        """加载所有已发现的插件"""
        results = []
        for plugin_id in self.discover_plugins():
            results.append(self.load_plugin(plugin_id))
        return results

    async def load_all_async(self) -> list[PluginInfo]:
        """异步加载所有已发现的插件"""
        results = []
        for plugin_id in self.discover_plugins():
            results.append(await self.load_plugin_async(plugin_id))
        return results

    def get_plugin(self, plugin_id: str) -> PluginInfo | None:
        """获取插件信息"""
        return self._plugins.get(plugin_id)

    def list_plugins(
        self,
        state: PluginState | None = None,
    ) -> list[PluginInfo]:
        """列出插件"""
        plugins = list(self._plugins.values())
        if state:
            plugins = [p for p in plugins if p.state == state]
        return plugins

    def get_stats(self) -> dict:
        """获取统计信息"""
        by_state = {}
        for p in self._plugins.values():
            state_name = p.state.value
            by_state[state_name] = by_state.get(state_name, 0) + 1

        return {
            "total": len(self._plugins),
            "by_state": by_state,
            "plugin_dir": self._plugin_dir,
        }

    # ------------------------------------------------------------------
    # 生命周期方法 (Stage PP)
    # ------------------------------------------------------------------

    def pause_plugin(self, plugin_id: str) -> bool:
        """暂停插件：LOADED/ACTIVE → PAUSED"""
        info = self._plugins.get(plugin_id)
        if not info:
            logger.warning(f"[PluginManager] 插件 '{plugin_id}' 不存在")
            return False
        if info.state != PluginState.LOADED:
            logger.warning(
                f"[PluginManager] 插件 '{plugin_id}' 状态为 {info.state}，无法暂停"
            )
            return False

        # 取消事件订阅
        for sub_id in info.subscriptions:
            self._event_bus.unsubscribe(sub_id)
        info.subscriptions.clear()

        info.state = PluginState.PAUSED
        if info.lifecycle:
            info.lifecycle.transition("paused", reason="pause_plugin called")
        logger.info(f"[PluginManager] 插件 '{plugin_id}' 已暂停")
        return True

    def resume_plugin(self, plugin_id: str) -> bool:
        """恢复插件：PAUSED → LOADED"""
        info = self._plugins.get(plugin_id)
        if not info:
            logger.warning(f"[PluginManager] 插件 '{plugin_id}' 不存在")
            return False
        if info.state != PluginState.PAUSED:
            logger.warning(
                f"[PluginManager] 插件 '{plugin_id}' 状态为 {info.state}，无法恢复"
            )
            return False

        # 重新注册事件订阅
        self._register_event_handlers(info)

        info.state = PluginState.LOADED
        if info.lifecycle:
            info.lifecycle.transition("loaded", reason="resume_plugin called")
        logger.info(f"[PluginManager] 插件 '{plugin_id}' 已恢复")
        return True

    def stop_plugin(self, plugin_id: str) -> bool:
        """停止插件：LOADED/PAUSED/ERROR/RETRYING → STOPPING → STOPPED"""
        info = self._plugins.get(plugin_id)
        if not info:
            logger.warning(f"[PluginManager] 插件 '{plugin_id}' 不存在")
            return False

        stoppable = {
            PluginState.LOADED,
            PluginState.PAUSED,
            PluginState.ERROR,
            PluginState.RETRYING,
        }
        if info.state not in stoppable:
            logger.warning(
                f"[PluginManager] 插件 '{plugin_id}' 状态为 {info.state}，无法停止"
            )
            return False

        info.state = PluginState.STOPPING
        if info.lifecycle:
            with contextlib.suppress(ValueError):
                info.lifecycle.transition("stopping", reason="stop_plugin called")

        try:
            # 取消事件订阅
            for sub_id in info.subscriptions:
                self._event_bus.unsubscribe(sub_id)
            info.subscriptions.clear()

            # 调用 on_unload（如果模块已加载）
            if info.module:
                on_unload = getattr(info.module, "on_unload", None)
                if on_unload and callable(on_unload):
                    with contextlib.suppress(Exception):
                        on_unload()

            info.state = PluginState.STOPPED
            info.module = None
            if info.lifecycle:
                info.lifecycle.transition("stopped", reason="stop succeeded")
            logger.info(f"[PluginManager] 插件 '{plugin_id}' 已停止")

        except Exception as exc:
            info.state = PluginState.ERROR
            info.error = str(exc)
            if info.lifecycle:
                info.lifecycle.record_error(f"stop failed: {exc}")
            logger.error(f"[PluginManager] 插件 '{plugin_id}' 停止失败: {exc}")
            return False

        return True

    def restart_plugin(self, plugin_id: str) -> PluginInfo | None:
        """重启插件：STOPPED/ERROR/DISABLED → LOADING → LOADED"""
        info = self._plugins.get(plugin_id)
        if not info:
            logger.warning(f"[PluginManager] 插件 '{plugin_id}' 不存在")
            return None

        restartable = {PluginState.STOPPED, PluginState.ERROR, PluginState.DISABLED}
        if info.state not in restartable:
            logger.warning(
                f"[PluginManager] 插件 '{plugin_id}' 状态为 {info.state}，无法重启"
            )
            return None

        config = info.config
        # 清除旧记录，触发全新加载
        if plugin_id in self._plugins:
            del self._plugins[plugin_id]

        return self.load_plugin(plugin_id, config=config)

    def disable_plugin(self, plugin_id: str) -> bool:
        """禁用插件：LOADED/STOPPED/ERROR → DISABLED"""
        info = self._plugins.get(plugin_id)
        if not info:
            logger.warning(f"[PluginManager] 插件 '{plugin_id}' 不存在")
            return False

        if info.state == PluginState.DISABLED:
            return True  # already disabled

        disableable = {
            PluginState.UNLOADED,
            PluginState.LOADED,
            PluginState.STOPPED,
            PluginState.ERROR,
            PluginState.PAUSED,
        }
        if info.state not in disableable:
            logger.warning(
                f"[PluginManager] 插件 '{plugin_id}' 状态为 {info.state}，无法禁用"
            )
            return False

        # 如果已加载，先取消订阅
        if info.state in (PluginState.LOADED, PluginState.PAUSED):
            for sub_id in info.subscriptions:
                self._event_bus.unsubscribe(sub_id)
            info.subscriptions.clear()

        info.state = PluginState.DISABLED
        if info.lifecycle:
            with contextlib.suppress(ValueError):
                info.lifecycle.transition("disabled", reason="disable_plugin called")
        logger.info(f"[PluginManager] 插件 '{plugin_id}' 已禁用")
        return True

    def enable_plugin(self, plugin_id: str) -> bool:
        """启用插件：DISABLED → UNLOADED（恢复为可加载状态）"""
        info = self._plugins.get(plugin_id)
        if not info:
            logger.warning(f"[PluginManager] 插件 '{plugin_id}' 不存在")
            return False

        if info.state != PluginState.DISABLED:
            logger.warning(
                f"[PluginManager] 插件 '{plugin_id}' 状态为 {info.state}，非禁用状态"
            )
            return False

        info.state = PluginState.UNLOADED
        if info.lifecycle:
            info.lifecycle.transition("unloaded", reason="enable_plugin called")
        logger.info(f"[PluginManager] 插件 '{plugin_id}' 已启用（可重新加载）")
        return True

    def health_check(self, plugin_id: str) -> dict | None:
        """单个插件健康检查"""
        info = self._plugins.get(plugin_id)
        if not info:
            return None
        return self._do_health_check(info)

    async def health_check_all(self) -> list[dict]:
        """批量健康检查（所有已加载插件）"""
        results = []
        for info in self._plugins.values():
            if info.state in (PluginState.LOADED, PluginState.PAUSED):
                results.append(await self._do_health_check_async(info))
        return results

    def _do_health_check(self, info: PluginInfo) -> dict:
        """同步执行单个插件的健康检查"""
        result = {
            "plugin_id": info.plugin_id,
            "name": info.name,
            "state": info.state.value,
            "healthy": None,
            "last_health_check": None,
            "error": info.error,
        }
        if info.lifecycle:
            import asyncio as _asyncio

            try:
                loop = _asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop and loop.is_running():
                # 在事件循环中运行（异步），无法同步等待 — 返回上次结果
                result["healthy"] = info.lifecycle.health_status
            else:
                # 无事件循环，同步运行
                import asyncio as _asyncio

                try:
                    healthy = _asyncio.run(info.lifecycle.run_health_check())
                    result["healthy"] = healthy
                except Exception:
                    result["healthy"] = info.lifecycle.health_status

            result["last_health_check"] = (
                info.lifecycle.last_health_check.isoformat()
                if info.lifecycle.last_health_check
                else None
            )
        return result

    async def _do_health_check_async(self, info: PluginInfo) -> dict:
        """异步执行单个插件的健康检查"""
        result = {
            "plugin_id": info.plugin_id,
            "name": info.name,
            "state": info.state.value,
            "healthy": None,
            "last_health_check": None,
            "error": info.error,
        }
        if info.lifecycle:
            result["healthy"] = await info.lifecycle.run_health_check()
            result["last_health_check"] = (
                info.lifecycle.last_health_check.isoformat()
                if info.lifecycle.last_health_check
                else None
            )
        return result

    # ------------------------------------------------------------------
    # 后台任务
    # ------------------------------------------------------------------

    def _ensure_health_monitor(self) -> None:
        """确保后台健康监控任务已启动"""
        if self._health_monitor_task is None or self._health_monitor_task.done():
            self._health_monitor_task = asyncio.ensure_future(
                self._background_health_monitor()
            )
            logger.info("[PluginManager] 后台健康监控已启动")

    def _ensure_retry_worker(self) -> None:
        """确保后台重试 worker 已启动"""
        if self._retry_worker_task is None or self._retry_worker_task.done():
            self._retry_worker_task = asyncio.ensure_future(self._auto_restart_worker())
            logger.info("[PluginManager] 后台重试 worker 已启动")

    async def _background_health_monitor(self) -> None:
        """后台健康监控：每 30s 检查所有 LOADED 插件"""
        while True:
            await asyncio.sleep(30)
            for info in list(self._plugins.values()):
                if info.state == PluginState.LOADED and info.lifecycle:
                    await info.lifecycle.run_health_check()
                    # 如果不健康且之前健康，记录并可选自动重启
                    if (
                        info.lifecycle.health_status is False
                        and info.lifecycle.should_auto_restart
                    ):
                        info.state = PluginState.RETRYING
                        info.lifecycle.retry_count += 1
                        with contextlib.suppress(ValueError):
                            info.lifecycle.transition(
                                "retrying",
                                reason=f"health check failed, auto-retry {info.lifecycle.retry_count}/{info.lifecycle.max_retries}",
                            )
                        self._ensure_retry_worker()

    async def _auto_restart_worker(self) -> None:
        """后台自动重试 worker：处理 RETRYING 状态的插件"""
        while True:
            # 查找所有 RETRYING 状态的插件
            retrying = [
                (pid, info)
                for pid, info in self._plugins.items()
                if info.state == PluginState.RETRYING and info.lifecycle
            ]
            if not retrying:
                await asyncio.sleep(5)
                continue

            for plugin_id, info in retrying:
                lifecycle = info.lifecycle
                if not lifecycle.should_auto_restart:
                    # 超过最大重试次数，设为 ERROR
                    info.state = PluginState.ERROR
                    with contextlib.suppress(ValueError):
                        lifecycle.transition(
                            "error",
                            reason=f"max_retries ({lifecycle.max_retries}) exceeded",
                        )
                    continue

                delay_ms = lifecycle.compute_retry_delay_ms()
                logger.info(
                    f"[PluginManager] 插件 '{plugin_id}' {delay_ms}ms 后自动重试 "
                    f"({lifecycle.retry_count}/{lifecycle.max_retries})"
                )
                await asyncio.sleep(delay_ms / 1000.0)

                # 重新加载
                try:
                    config = info.config
                    # 保留 lifecycle 引用
                    saved_lifecycle = info.lifecycle
                    del self._plugins[plugin_id]
                    new_info = self.load_plugin(plugin_id, config=config)
                    # 恢复 lifecycle 历史
                    if new_info.lifecycle:
                        new_info.lifecycle.retry_count = saved_lifecycle.retry_count
                    logger.info(
                        f"[PluginManager] 插件 '{plugin_id}' 自动重试加载"
                        f"{'成功' if new_info.state == PluginState.LOADED else '失败'}"
                    )
                except Exception as exc:
                    logger.error(
                        f"[PluginManager] 插件 '{plugin_id}' 自动重试失败: {exc}"
                    )

            await asyncio.sleep(2)

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _import_plugin(self, plugin_id: str):
        """导入插件模块"""
        if not self._plugin_dir:
            raise ValueError(f"插件目录未配置，无法加载 '{plugin_id}'")

        plugin_path = os.path.join(self._plugin_dir, plugin_id)

        # 每次加载使用唯一模块名，避免字节码缓存导致热重载读取旧内容
        module_name = f"_plugins_{plugin_id}_{id(self)}_{len(self._plugins)}"

        # 清除旧模块缓存和 .pyc 字节码缓存
        for key in list(sys.modules.keys()):
            if key.startswith(f"_plugins_{plugin_id}_"):
                del sys.modules[key]
        importlib.invalidate_caches()
        self._clear_pycache(plugin_id)

        # 尝试作为包加载
        init_path = os.path.join(plugin_path, "__init__.py")
        if os.path.isfile(init_path):
            spec = importlib.util.spec_from_file_location(
                module_name,
                init_path,
                submodule_search_locations=[plugin_path],
            )
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                spec.loader.exec_module(module)
                return module

        # 尝试作为单文件加载
        file_path = os.path.join(self._plugin_dir, f"{plugin_id}.py")
        if os.path.isfile(file_path):
            spec = importlib.util.spec_from_file_location(module_name, file_path)
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                spec.loader.exec_module(module)
                return module

        return None

    def _clear_pycache(self, plugin_id: str) -> None:
        """清除插件的 .pyc 字节码缓存"""
        if not self._plugin_dir:
            return

        # 清除单文件的 .pyc
        pycache_dir = os.path.join(self._plugin_dir, "__pycache__")
        if os.path.isdir(pycache_dir):
            for fname in os.listdir(pycache_dir):
                if fname.startswith(plugin_id) and fname.endswith(".pyc"):
                    with contextlib.suppress(OSError):
                        os.remove(os.path.join(pycache_dir, fname))

        # 清除包的 .pyc
        pkg_dir = os.path.join(self._plugin_dir, plugin_id)
        if os.path.isdir(pkg_dir):
            pkg_cache = os.path.join(pkg_dir, "__pycache__")
            if os.path.isdir(pkg_cache):
                for fname in os.listdir(pkg_cache):
                    if fname.endswith(".pyc"):
                        with contextlib.suppress(OSError):
                            os.remove(os.path.join(pkg_cache, fname))

    def _check_dependencies(self, info: PluginInfo) -> list[str]:
        """检查插件依赖"""
        missing = []
        for dep_id in info.dependencies:
            dep = self._plugins.get(dep_id)
            if not dep or dep.state != PluginState.LOADED:
                missing.append(dep_id)
        return missing

    def _register_event_handlers(self, info: PluginInfo) -> None:
        """注册插件的事件处理器"""
        if not info.module:
            return

        # 查找 on_event 处理器
        on_event = getattr(info.module, "on_event", None)
        if on_event and callable(on_event):
            # 订阅所有事件
            sub_id = self._event_bus.subscribe("*", on_event)
            info.subscriptions.append(sub_id)
            logger.debug(f"[PluginManager] 插件 '{info.plugin_id}' 订阅: *")

        # 查找命名约定的事件处理器: on_<topic>
        # 例如: on_tool_start, on_tool_complete
        for name in dir(info.module):
            if name.startswith("on_") and name not in (
                "on_load",
                "on_unload",
                "on_error",
                "on_event",
            ):
                handler = getattr(info.module, name)
                if callable(handler):
                    # on_tool_start -> tool.start
                    topic = name[3:]  # 去掉 "on_"
                    topic = topic.replace("_", ".")
                    sub_id = self._event_bus.subscribe(topic, handler)
                    info.subscriptions.append(sub_id)
                    logger.debug(
                        f"[PluginManager] 插件 '{info.plugin_id}' 订阅: {topic}"
                    )
