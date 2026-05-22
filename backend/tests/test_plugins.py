"""
插件系统测试
- EventBus 订阅/发布/通配符/优先级
- PluginManager 加载/卸载/热重载/依赖
- API 端点
"""

import os
import tempfile

import pytest

from app.modules.plugins.event_bus import EventBus
from app.modules.plugins.manager import (
    PluginManager,
    PluginState,
)

# ============================================================
# EventBus 测试
# ============================================================


class TestEventBusSubscribe:
    """测试订阅"""

    def test_subscribe_returns_id(self):
        """测试订阅返回 ID"""
        bus = EventBus()
        sub_id = bus.subscribe("tool.start", lambda t, d: None)
        assert sub_id.startswith("sub_")

    def test_subscribe_multiple(self):
        """测试多个订阅"""
        bus = EventBus()
        bus.subscribe("tool.start", lambda t, d: None)
        bus.subscribe("tool.start", lambda t, d: None)
        subs = bus.get_subscriptions("tool.start")
        assert len(subs) == 2


class TestEventBusUnsubscribe:
    """测试取消订阅"""

    def test_unsubscribe(self):
        """测试取消订阅"""
        bus = EventBus()
        sub_id = bus.subscribe("tool.start", lambda t, d: None)
        result = bus.unsubscribe(sub_id)
        assert result is True
        subs = bus.get_subscriptions("tool.start")
        assert len(subs) == 0

    def test_unsubscribe_unknown(self):
        """测试取消不存在的订阅"""
        bus = EventBus()
        result = bus.unsubscribe("sub_999")
        assert result is False

    def test_unsubscribe_all(self):
        """测试取消所有订阅"""
        bus = EventBus()
        bus.subscribe("tool.start", lambda t, d: None)
        bus.subscribe("tool.complete", lambda t, d: None)
        count = bus.unsubscribe_all()
        assert count == 2

    def test_unsubscribe_all_by_topic(self):
        """测试按主题取消订阅"""
        bus = EventBus()
        bus.subscribe("tool.start", lambda t, d: None)
        bus.subscribe("tool.complete", lambda t, d: None)
        count = bus.unsubscribe_all("tool.start")
        assert count == 1
        assert len(bus.get_subscriptions("tool.start")) == 0
        assert len(bus.get_subscriptions("tool.complete")) == 1


class TestEventBusPublish:
    """测试发布"""

    @pytest.mark.asyncio
    async def test_publish_sync_handler(self):
        """测试同步处理器"""
        bus = EventBus()
        received = []

        def handler(topic, data):
            received.append((topic, data))

        bus.subscribe("test.event", handler)
        fired = await bus.publish("test.event", {"key": "value"})

        assert fired == 1
        assert len(received) == 1
        assert received[0] == ("test.event", {"key": "value"})

    @pytest.mark.asyncio
    async def test_publish_async_handler(self):
        """测试异步处理器"""
        bus = EventBus()
        received = []

        async def handler(topic, data):
            received.append((topic, data))

        bus.subscribe("test.event", handler)
        fired = await bus.publish("test.event", {"key": "value"})

        assert fired == 1
        assert len(received) == 1

    @pytest.mark.asyncio
    async def test_publish_no_subscribers(self):
        """测试无订阅者"""
        bus = EventBus()
        fired = await bus.publish("test.event", {})
        assert fired == 0

    @pytest.mark.asyncio
    async def test_publish_default_data(self):
        """测试默认空数据"""
        bus = EventBus()
        received = []

        def handler(topic, data):
            received.append(data)

        bus.subscribe("test.event", handler)
        await bus.publish("test.event")
        assert received[0] == {}


class TestEventBusWildcard:
    """测试通配符"""

    @pytest.mark.asyncio
    async def test_wildcard_dot_star(self):
        """测试 tool.* 匹配"""
        bus = EventBus()
        received = []

        def handler(topic, data):
            received.append(topic)

        bus.subscribe("tool.*", handler)
        await bus.publish("tool.start", {})
        await bus.publish("tool.complete", {})
        await bus.publish("other.event", {})

        assert "tool.start" in received
        assert "tool.complete" in received
        assert "other.event" not in received

    @pytest.mark.asyncio
    async def test_wildcard_double_star(self):
        """测试 ** 匹配多层"""
        bus = EventBus()
        received = []

        def handler(topic, data):
            received.append(topic)

        bus.subscribe("agent.**", handler)
        await bus.publish("agent.tool.start", {})
        await bus.publish("agent.reasoning.step", {})
        await bus.publish("other.event", {})

        assert "agent.tool.start" in received
        assert "agent.reasoning.step" in received
        assert "other.event" not in received

    @pytest.mark.asyncio
    async def test_wildcard_star_all(self):
        """测试 * 匹配所有"""
        bus = EventBus()
        received = []

        def handler(topic, data):
            received.append(topic)

        bus.subscribe("*", handler)
        await bus.publish("tool.start", {})
        await bus.publish("any.event", {})

        assert len(received) == 2


class TestEventBusPriority:
    """测试优先级"""

    @pytest.mark.asyncio
    async def test_priority_order(self):
        """测试高优先级先执行"""
        bus = EventBus()
        order = []

        def low_handler(topic, data):
            order.append("low")

        def high_handler(topic, data):
            order.append("high")

        bus.subscribe("test.event", low_handler, priority=0)
        bus.subscribe("test.event", high_handler, priority=10)
        await bus.publish("test.event", {})

        assert order == ["high", "low"]


class TestEventBusErrorHandling:
    """测试错误处理"""

    @pytest.mark.asyncio
    async def test_handler_error_doesnt_stop_others(self):
        """测试处理器异常不影响其他处理器"""
        bus = EventBus()
        results = []

        def bad_handler(topic, data):
            raise RuntimeError("oops")

        def good_handler(topic, data):
            results.append("ok")

        bus.subscribe("test.event", bad_handler)
        bus.subscribe("test.event", good_handler)
        fired = await bus.publish("test.event", {})

        assert fired == 1  # 只有 good_handler 成功
        assert "ok" in results


class TestEventBusGetSubscriptions:
    """测试获取订阅信息"""

    def test_get_all_subscriptions(self):
        """测试获取所有订阅"""
        bus = EventBus()
        bus.subscribe("tool.start", lambda t, d: None)
        bus.subscribe("tool.*", lambda t, d: None)

        subs = bus.get_subscriptions()
        assert len(subs) == 2

    def test_get_subscriptions_by_topic(self):
        """测试按主题获取订阅"""
        bus = EventBus()
        bus.subscribe("tool.start", lambda t, d: None)
        bus.subscribe("other.event", lambda t, d: None)

        subs = bus.get_subscriptions("tool.start")
        assert len(subs) == 1
        assert subs[0]["topic"] == "tool.start"

    def test_subscription_info_fields(self):
        """测试订阅信息字段"""
        bus = EventBus()

        def my_handler(topic, data):
            pass

        bus.subscribe("test.event", my_handler, priority=5)
        subs = bus.get_subscriptions("test.event")
        assert len(subs) == 1
        assert subs[0]["handler"] == "my_handler"
        assert subs[0]["priority"] == 5


# ============================================================
# PluginManager 测试
# ============================================================


class TestPluginManagerBasic:
    """测试基本功能"""

    def test_create_manager(self):
        """测试创建管理器"""
        manager = PluginManager()
        assert manager.event_bus is not None
        assert len(manager.list_plugins()) == 0

    def test_create_manager_with_event_bus(self):
        """测试传入事件总线"""
        bus = EventBus()
        manager = PluginManager(event_bus=bus)
        assert manager.event_bus is bus

    def test_get_stats_empty(self):
        """测试空统计"""
        manager = PluginManager()
        stats = manager.get_stats()
        assert stats["total"] == 0


class TestPluginDiscovery:
    """测试插件发现"""

    def test_discover_no_dir(self):
        """测试无插件目录"""
        manager = PluginManager()
        plugins = manager.discover_plugins()
        assert plugins == []

    def test_discover_empty_dir(self):
        """测试空目录"""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = PluginManager(plugin_dir=tmpdir)
            plugins = manager.discover_plugins()
            assert plugins == []

    def test_discover_py_file(self):
        """测试发现 .py 文件插件"""
        with tempfile.TemporaryDirectory() as tmpdir:
            # 创建插件文件
            plugin_path = os.path.join(tmpdir, "my_plugin.py")
            with open(plugin_path, "w") as f:
                f.write("__plugin_name__ = 'My Plugin'\n")

            manager = PluginManager(plugin_dir=tmpdir)
            plugins = manager.discover_plugins()
            assert "my_plugin" in plugins

    def test_discover_package(self):
        """测试发现包插件"""
        with tempfile.TemporaryDirectory() as tmpdir:
            pkg_dir = os.path.join(tmpdir, "my_pkg")
            os.makedirs(pkg_dir)
            with open(os.path.join(pkg_dir, "__init__.py"), "w") as f:
                f.write("__plugin_name__ = 'My Package'\n")

            manager = PluginManager(plugin_dir=tmpdir)
            plugins = manager.discover_plugins()
            assert "my_pkg" in plugins

    def test_discover_ignores_private(self):
        """测试忽略私有文件"""
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, "_private.py"), "w") as f:
                f.write("pass\n")

            manager = PluginManager(plugin_dir=tmpdir)
            plugins = manager.discover_plugins()
            assert "_private" not in plugins


class TestPluginLoad:
    """测试插件加载"""

    def test_load_simple_plugin(self):
        """测试加载简单插件"""
        with tempfile.TemporaryDirectory() as tmpdir:
            plugin_path = os.path.join(tmpdir, "hello.py")
            with open(plugin_path, "w") as f:
                f.write(
                    "__plugin_name__ = 'Hello'\n"
                    "__plugin_version__ = '1.0.0'\n"
                    "__plugin_description__ = 'A hello plugin'\n"
                    "__plugin_author__ = 'Test'\n"
                )

            manager = PluginManager(plugin_dir=tmpdir)
            info = manager.load_plugin("hello")

            assert info.state == PluginState.LOADED
            assert info.name == "Hello"
            assert info.version == "1.0.0"
            assert info.description == "A hello plugin"
            assert info.author == "Test"

    def test_load_plugin_not_found(self):
        """测试加载不存在的插件"""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = PluginManager(plugin_dir=tmpdir)
            info = manager.load_plugin("nonexistent")

            assert info.state == PluginState.ERROR
            assert "未找到" in info.error

    def test_load_plugin_no_dir(self):
        """测试无插件目录"""
        manager = PluginManager()
        info = manager.load_plugin("test")
        assert info.state == PluginState.ERROR

    @pytest.mark.asyncio
    async def test_load_plugin_async(self):
        """测试异步加载插件"""
        with tempfile.TemporaryDirectory() as tmpdir:
            plugin_path = os.path.join(tmpdir, "async_plugin.py")
            with open(plugin_path, "w") as f:
                f.write("__plugin_name__ = 'AsyncPlugin'\non_load = None\n")

            manager = PluginManager(plugin_dir=tmpdir)
            info = await manager.load_plugin_async("async_plugin")
            assert info.state == PluginState.LOADED

    def test_load_plugin_with_on_load(self):
        """测试加载含 on_load 钩子的插件"""
        with tempfile.TemporaryDirectory() as tmpdir:
            plugin_path = os.path.join(tmpdir, "hooked.py")
            with open(plugin_path, "w") as f:
                f.write(
                    "__plugin_name__ = 'Hooked'\n"
                    "load_called = False\n"
                    "def on_load(bus, config):\n"
                    "    global load_called\n"
                    "    load_called = True\n"
                )

            manager = PluginManager(plugin_dir=tmpdir)
            info = manager.load_plugin("hooked")
            assert info.state == PluginState.LOADED


class TestPluginUnload:
    """测试插件卸载"""

    def test_unload_loaded_plugin(self):
        """测试卸载已加载插件"""
        with tempfile.TemporaryDirectory() as tmpdir:
            plugin_path = os.path.join(tmpdir, "removable.py")
            with open(plugin_path, "w") as f:
                f.write("__plugin_name__ = 'Removable'\n")

            manager = PluginManager(plugin_dir=tmpdir)
            manager.load_plugin("removable")
            result = manager.unload_plugin("removable")

            assert result is True
            info = manager.get_plugin("removable")
            assert info.state == PluginState.UNLOADED

    def test_unload_not_loaded(self):
        """测试卸载未加载插件"""
        manager = PluginManager()
        result = manager.unload_plugin("nonexistent")
        assert result is False

    def test_unload_with_on_unload_hook(self):
        """测试卸载时调用 on_unload"""
        with tempfile.TemporaryDirectory() as tmpdir:
            plugin_path = os.path.join(tmpdir, "cleanup.py")
            with open(plugin_path, "w") as f:
                f.write(
                    "__plugin_name__ = 'Cleanup'\n"
                    "unload_called = False\n"
                    "def on_unload():\n"
                    "    global unload_called\n"
                    "    unload_called = True\n"
                )

            manager = PluginManager(plugin_dir=tmpdir)
            manager.load_plugin("cleanup")
            result = manager.unload_plugin("cleanup")
            assert result is True


class TestPluginReload:
    """测试热重载"""

    def test_reload_plugin(self):
        """测试重载插件"""
        with tempfile.TemporaryDirectory() as tmpdir:
            plugin_path = os.path.join(tmpdir, "reloadable.py")
            with open(plugin_path, "w") as f:
                f.write("__plugin_name__ = 'Reloadable v1'\n")

            manager = PluginManager(plugin_dir=tmpdir)
            info1 = manager.load_plugin("reloadable")
            assert info1.name == "Reloadable v1"

            # 修改插件
            with open(plugin_path, "w") as f:
                f.write("__plugin_name__ = 'Reloadable v2'\n")

            info2 = manager.reload_plugin("reloadable")
            assert info2.name == "Reloadable v2"
            assert info2.state == PluginState.LOADED


class TestPluginDependencies:
    """测试依赖检查"""

    def test_load_with_missing_dependency(self):
        """测试依赖缺失"""
        with tempfile.TemporaryDirectory() as tmpdir:
            plugin_path = os.path.join(tmpdir, "dependent.py")
            with open(plugin_path, "w") as f:
                f.write(
                    "__plugin_name__ = 'Dependent'\n"
                    "__plugin_dependencies__ = ['missing_plugin']\n"
                )

            manager = PluginManager(plugin_dir=tmpdir)
            info = manager.load_plugin("dependent")
            assert info.state == PluginState.ERROR
            assert "missing_plugin" in info.error

    def test_load_with_satisfied_dependency(self):
        """测试依赖满足"""
        with tempfile.TemporaryDirectory() as tmpdir:
            # 先创建被依赖的插件
            dep_path = os.path.join(tmpdir, "base_plugin.py")
            with open(dep_path, "w") as f:
                f.write("__plugin_name__ = 'Base'\n")

            # 创建依赖插件
            dep_plugin_path = os.path.join(tmpdir, "advanced_plugin.py")
            with open(dep_plugin_path, "w") as f:
                f.write(
                    "__plugin_name__ = 'Advanced'\n"
                    "__plugin_dependencies__ = ['base_plugin']\n"
                )

            manager = PluginManager(plugin_dir=tmpdir)
            manager.load_plugin("base_plugin")
            info = manager.load_plugin("advanced_plugin")
            assert info.state == PluginState.LOADED


class TestPluginEventHandlers:
    """测试插件事件处理器自动注册"""

    def test_on_event_handler(self):
        """测试 on_event 处理器注册"""
        with tempfile.TemporaryDirectory() as tmpdir:
            plugin_path = os.path.join(tmpdir, "eventful.py")
            with open(plugin_path, "w") as f:
                f.write(
                    "__plugin_name__ = 'Eventful'\n"
                    "def on_event(topic, data):\n"
                    "    pass\n"
                )

            bus = EventBus()
            manager = PluginManager(event_bus=bus, plugin_dir=tmpdir)
            info = manager.load_plugin("eventful")

            assert info.state == PluginState.LOADED
            assert len(info.subscriptions) == 1  # on_event -> "*"

    def test_named_event_handlers(self):
        """测试命名事件处理器注册"""
        with tempfile.TemporaryDirectory() as tmpdir:
            plugin_path = os.path.join(tmpdir, "named.py")
            with open(plugin_path, "w") as f:
                f.write(
                    "__plugin_name__ = 'Named'\n"
                    "def on_tool_start(topic, data):\n"
                    "    pass\n"
                    "def on_tool_complete(topic, data):\n"
                    "    pass\n"
                )

            bus = EventBus()
            manager = PluginManager(event_bus=bus, plugin_dir=tmpdir)
            info = manager.load_plugin("named")

            assert info.state == PluginState.LOADED
            assert len(info.subscriptions) == 2

    def test_unload_removes_subscriptions(self):
        """测试卸载时移除订阅"""
        with tempfile.TemporaryDirectory() as tmpdir:
            plugin_path = os.path.join(tmpdir, "sub.py")
            with open(plugin_path, "w") as f:
                f.write(
                    "__plugin_name__ = 'Sub'\ndef on_event(topic, data):\n    pass\n"
                )

            bus = EventBus()
            manager = PluginManager(event_bus=bus, plugin_dir=tmpdir)
            info = manager.load_plugin("sub")
            assert len(info.subscriptions) > 0

            manager.unload_plugin("sub")
            assert len(info.subscriptions) == 0


class TestPluginListAndStats:
    """测试列表和统计"""

    def test_list_plugins(self):
        """测试列出插件"""
        with tempfile.TemporaryDirectory() as tmpdir:
            for name in ["a", "b", "c"]:
                with open(os.path.join(tmpdir, f"{name}.py"), "w") as f:
                    f.write(f"__plugin_name__ = '{name}'\n")

            manager = PluginManager(plugin_dir=tmpdir)
            manager.load_plugin("a")
            manager.load_plugin("b")

            plugins = manager.list_plugins()
            assert len(plugins) == 2

    def test_list_plugins_by_state(self):
        """测试按状态筛选"""
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, "ok.py"), "w") as f:
                f.write("__plugin_name__ = 'OK'\n")

            manager = PluginManager(plugin_dir=tmpdir)
            manager.load_plugin("ok")

            loaded = manager.list_plugins(state=PluginState.LOADED)
            assert len(loaded) == 1

            unloaded = manager.list_plugins(state=PluginState.UNLOADED)
            assert len(unloaded) == 0

    def test_get_stats(self):
        """测试统计"""
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, "s.py"), "w") as f:
                f.write("__plugin_name__ = 'S'\n")

            manager = PluginManager(plugin_dir=tmpdir)
            manager.load_plugin("s")

            stats = manager.get_stats()
            assert stats["total"] == 1
            assert stats["by_state"].get("loaded", 0) == 1

    def test_get_plugin(self):
        """测试获取单个插件"""
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, "single.py"), "w") as f:
                f.write("__plugin_name__ = 'Single'\n")

            manager = PluginManager(plugin_dir=tmpdir)
            manager.load_plugin("single")

            info = manager.get_plugin("single")
            assert info is not None
            assert info.name == "Single"

            none_info = manager.get_plugin("nonexistent")
            assert none_info is None


class TestPluginLoadAll:
    """测试批量加载"""

    def test_load_all(self):
        """测试加载所有插件"""
        with tempfile.TemporaryDirectory() as tmpdir:
            for name in ["x", "y"]:
                with open(os.path.join(tmpdir, f"{name}.py"), "w") as f:
                    f.write(f"__plugin_name__ = '{name.upper()}'\n")

            manager = PluginManager(plugin_dir=tmpdir)
            results = manager.load_all()
            assert len(results) == 2
            assert all(r.state == PluginState.LOADED for r in results)
