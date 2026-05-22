"""
PioneClawPlugin 基类 + plugin_metadata 装饰器

借鉴 OpenClaw packages/plugin-sdk/ 的插件入口点 API

第三方插件开发者继承 PioneClawPlugin 并覆写生命周期钩子。
"""

from typing import Any

from .event_types import PluginEvent


class PioneClawPlugin:
    """插件基类，第三方开发者继承此类

    借鉴 OpenClaw plugin-sdk Plugin 基类

    用法:
        @plugin_metadata(id="my-plugin", name="我的插件", version="1.0.0")
        class MyPlugin(PioneClawPlugin):
            async def on_load(self):
                print(f"{self.plugin_name} loaded!")

            async def on_event(self, event: PluginEvent):
                if event.type == EventType.TOOL_START:
                    print(f"Tool started: {event.data['tool_name']}")
    """

    # 插件元数据（子类或装饰器设置）
    plugin_id: str = ""
    plugin_name: str = ""
    version: str = "1.0.0"
    description: str = ""
    dependencies: list[str] = []

    # 生命周期钩子（子类覆写）
    async def on_load(self) -> None:
        """插件加载时调用"""
        pass

    async def on_unload(self) -> None:
        """插件卸载时调用"""
        pass

    async def on_error(self, error: Exception) -> None:
        """插件出错时调用"""
        pass

    # 事件订阅（子类覆写）
    async def on_event(self, event: PluginEvent) -> None:
        """接收所有事件"""
        pass

    def get_info(self) -> dict[str, Any]:
        """获取插件信息"""
        return {
            "plugin_id": self.plugin_id,
            "plugin_name": self.plugin_name,
            "version": self.version,
            "description": self.description,
            "dependencies": self.dependencies,
        }


def plugin_metadata(
    id: str,
    name: str,
    version: str = "1.0.0",
    description: str = "",
    **kwargs,
):
    """插件元数据装饰器

    借鉴 OpenClaw plugin-sdk plugin_metadata()

    用法:
        @plugin_metadata(id="my-plugin", name="我的插件")
        class MyPlugin(PioneClawPlugin):
            ...
    """

    def decorator(cls):
        cls.plugin_id = id
        cls.plugin_name = name
        cls.version = version
        cls.description = description
        for k, v in kwargs.items():
            setattr(cls, k, v)
        return cls

    return decorator
