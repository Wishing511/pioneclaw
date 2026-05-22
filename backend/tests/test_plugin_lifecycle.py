"""
插件生命周期测试 (Stage PP)
- PluginLifecycle 状态机
- PluginManager 生命周期方法 (pause/resume/stop/restart/enable/disable)
- 健康检查
- API 端点
"""

import asyncio
import os
import tempfile

import pytest

from app.modules.plugins.lifecycle import PluginLifecycle
from app.modules.plugins.manager import (
    PluginInfo,
    PluginManager,
    PluginState,
)

# ============================================================
# PluginLifecycle 状态机测试
# ============================================================


class TestPluginStateMachine:
    """测试状态转换"""

    def setup_method(self):
        self.lifecycle = PluginLifecycle(plugin_id="test_plugin")

    def test_initial_state(self):
        """初始状态为 unloaded"""
        assert self.lifecycle.state == "unloaded"
        assert self.lifecycle.retry_count == 0

    def test_valid_transition_unloaded_to_loading(self):
        """UNLOADED → LOADING 合法"""
        t = self.lifecycle.transition("loading", reason="load")
        assert self.lifecycle.state == "loading"
        assert t.from_state == "unloaded"
        assert t.to_state == "loading"
        assert len(self.lifecycle.transitions) == 1

    def test_valid_transition_loading_to_loaded(self):
        """LOADING → LOADED 合法"""
        self.lifecycle.transition("loading")
        t = self.lifecycle.transition("loaded", reason="success")
        assert self.lifecycle.state == "loaded"
        assert t.reason == "success"

    def test_valid_transition_loading_to_error(self):
        """LOADING → ERROR 合法"""
        self.lifecycle.transition("loading")
        self.lifecycle.transition("error", reason="import failed")
        assert self.lifecycle.state == "error"

    def test_valid_transition_loaded_to_paused(self):
        """LOADED → PAUSED 合法"""
        self.lifecycle.transition("loading")
        self.lifecycle.transition("loaded")
        self.lifecycle.transition("paused", reason="manual pause")
        assert self.lifecycle.state == "paused"

    def test_valid_transition_paused_to_loaded(self):
        """PAUSED → LOADED 合法"""
        self.lifecycle.transition("loading")
        self.lifecycle.transition("loaded")
        self.lifecycle.transition("paused")
        self.lifecycle.transition("loaded", reason="resume")
        assert self.lifecycle.state == "loaded"

    def test_valid_transition_loaded_to_stopping(self):
        """LOADED → STOPPING 合法"""
        self.lifecycle.transition("loading")
        self.lifecycle.transition("loaded")
        self.lifecycle.transition("stopping", reason="stop requested")
        assert self.lifecycle.state == "stopping"

    def test_valid_transition_stopping_to_stopped(self):
        """STOPPING → STOPPED 合法"""
        self.lifecycle.transition("loading")
        self.lifecycle.transition("loaded")
        self.lifecycle.transition("stopping")
        self.lifecycle.transition("stopped", reason="stop complete")
        assert self.lifecycle.state == "stopped"

    def test_valid_transition_stopped_to_loading(self):
        """STOPPED → LOADING 合法（重启）"""
        self.lifecycle.transition("loading")
        self.lifecycle.transition("loaded")
        self.lifecycle.transition("stopping")
        self.lifecycle.transition("stopped")
        self.lifecycle.transition("loading", reason="restart")
        assert self.lifecycle.state == "loading"

    def test_valid_transition_loaded_to_disabled(self):
        """LOADED → DISABLED 合法"""
        self.lifecycle.transition("loading")
        self.lifecycle.transition("loaded")
        self.lifecycle.transition("disabled", reason="admin disabled")
        assert self.lifecycle.state == "disabled"

    def test_valid_transition_disabled_to_unloaded(self):
        """DISABLED → UNLOADED 合法（重新启用）"""
        self.lifecycle.transition("loading")
        self.lifecycle.transition("loaded")
        self.lifecycle.transition("disabled")
        self.lifecycle.transition("unloaded", reason="enable")
        assert self.lifecycle.state == "unloaded"

    def test_invalid_transition_raises(self):
        """非法状态转换抛出 ValueError"""
        self.lifecycle.transition("loading")
        self.lifecycle.transition("loaded")
        with pytest.raises(ValueError, match="非法状态转换"):
            self.lifecycle.transition("unloaded")  # loaded → unloaded 不合法

    def test_transition_history(self):
        """转换历史记录"""
        self.lifecycle.transition("loading", "load")
        self.lifecycle.transition("loaded", "done")
        assert len(self.lifecycle.transitions) == 2
        assert self.lifecycle.last_transition.to_state == "loaded"

    def test_transition_history_limit(self):
        """转换历史最多保留 50 条"""
        for i in range(60):
            try:
                # loading ↔ error 来回切换
                if self.lifecycle.state == "loading":
                    self.lifecycle.transition("error", f"err {i}")
                elif self.lifecycle.state == "error":
                    self.lifecycle.transition("loading", f"retry {i}")
                else:
                    self.lifecycle.transition("loading", f"start {i}")
            except ValueError:
                self.lifecycle = PluginLifecycle(plugin_id="test")
                self.lifecycle.transition("loading", "reset")
        assert len(self.lifecycle.transitions) <= 50

    def test_can_transition(self):
        """can_transition 检查"""
        assert self.lifecycle.can_transition("loading") is True
        assert self.lifecycle.can_transition("loaded") is False  # 不能跳过 loading
        self.lifecycle.transition("loading")
        assert self.lifecycle.can_transition("loaded") is True
        assert self.lifecycle.can_transition("error") is True


# ============================================================
# 健康检查测试
# ============================================================


class TestPluginHealthCheck:
    """测试健康检查"""

    def test_health_check_without_fn(self):
        """无自定义健康检查函数时默认健康"""
        lifecycle = PluginLifecycle(plugin_id="test")
        # 使用 asyncio 来运行异步方法
        healthy = asyncio.run(lifecycle.run_health_check())
        assert healthy is True
        assert lifecycle.health_status is True
        assert lifecycle.last_health_check is not None

    def test_health_check_passes(self):
        """健康检查通过"""
        lifecycle = PluginLifecycle(plugin_id="test", health_check_fn=lambda: True)
        healthy = asyncio.run(lifecycle.run_health_check())
        assert healthy is True
        assert lifecycle.is_healthy is True

    def test_health_check_fails(self):
        """健康检查失败"""
        lifecycle = PluginLifecycle(plugin_id="test", health_check_fn=lambda: False)
        healthy = asyncio.run(lifecycle.run_health_check())
        assert healthy is False
        assert lifecycle.is_healthy is False

    def test_health_check_exception(self):
        """健康检查抛出异常时不崩溃"""

        def bad_check():
            raise RuntimeError("connection lost")

        lifecycle = PluginLifecycle(plugin_id="test", health_check_fn=bad_check)
        healthy = asyncio.run(lifecycle.run_health_check())
        assert healthy is False
        assert len(lifecycle.error_history) >= 1

    def test_set_health_check_fn(self):
        """动态设置健康检查函数"""
        lifecycle = PluginLifecycle(plugin_id="test")
        lifecycle.set_health_check_fn(lambda: False)
        healthy = asyncio.run(lifecycle.run_health_check())
        assert healthy is False

    def test_paused_at_tracking(self):
        """暂停/恢复时间戳追踪"""
        lifecycle = PluginLifecycle(plugin_id="test")
        assert lifecycle.paused_at is None
        lifecycle.transition("loading")
        lifecycle.transition("loaded")
        lifecycle.transition("paused")
        assert lifecycle.paused_at is not None
        lifecycle.transition("loaded")
        assert lifecycle.paused_at is None  # 恢复后清零

    def test_stopped_at_tracking(self):
        """停止时间戳追踪"""
        lifecycle = PluginLifecycle(plugin_id="test")
        lifecycle.transition("loading")
        lifecycle.transition("loaded")
        lifecycle.transition("stopping")
        lifecycle.transition("stopped")
        assert lifecycle.stopped_at is not None
        assert lifecycle.health_status is None  # 停止后健康状态清空


# ============================================================
# 自动重试测试
# ============================================================


class TestPluginAutoRetry:
    """测试自动重试"""

    def test_should_auto_restart_in_retrying(self):
        """RETRYING 状态且未超过 max_retries 时应自动重启"""
        lifecycle = PluginLifecycle(plugin_id="test", max_retries=3)
        lifecycle.transition("loading")
        lifecycle.transition("error", "failed")
        lifecycle.transition("retrying", "auto retry")
        lifecycle.retry_count = 1
        assert lifecycle.should_auto_restart is True

    def test_should_not_auto_restart_when_exceeded(self):
        """超过 max_retries 后不应自动重启"""
        lifecycle = PluginLifecycle(plugin_id="test", max_retries=3)
        lifecycle.transition("loading")
        lifecycle.transition("error", "failed")
        lifecycle.transition("retrying")
        lifecycle.retry_count = 3
        assert lifecycle.should_auto_restart is False

    def test_compute_retry_delay_increases(self):
        """重试延迟应指数增长"""
        lifecycle = PluginLifecycle(plugin_id="test", max_retries=3)
        lifecycle.retry_count = 1
        d1 = lifecycle.compute_retry_delay_ms()
        lifecycle.retry_count = 2
        d2 = lifecycle.compute_retry_delay_ms()
        assert d2 >= d1  # 第二次应更长

    def test_compute_retry_delay_max_cap(self):
        """重试延迟不超过最大值"""
        lifecycle = PluginLifecycle(plugin_id="test", max_retries=10)
        lifecycle.retry_count = 10
        delay = lifecycle.compute_retry_delay_ms()
        assert delay <= lifecycle.RETRY_MAX_MS

    def test_reset_retry(self):
        """reset_retry 清零计数"""
        lifecycle = PluginLifecycle(plugin_id="test", max_retries=3)
        lifecycle.transition("loading")
        lifecycle.transition("error")
        lifecycle.retry_count = 2
        lifecycle.reset_retry()
        assert lifecycle.retry_count == 0

    def test_error_history_records(self):
        """错误历史记录"""
        lifecycle = PluginLifecycle(plugin_id="test")
        lifecycle.transition("loading")
        lifecycle.transition("error", "import error")
        lifecycle.record_error("runtime error 1")
        lifecycle.record_error("runtime error 2")
        assert len(lifecycle.error_history) >= 3

    def test_to_dict(self):
        """序列化输出包含所有关键字段"""
        lifecycle = PluginLifecycle(plugin_id="test")
        lifecycle.transition("loading")
        lifecycle.transition("loaded")
        d = lifecycle.to_dict()
        assert d["state"] == "loaded"
        assert d["retry_count"] == 0
        assert "health_status" in d
        assert "error_history" in d
        assert "last_transition" in d


# ============================================================
# PluginManager 生命周期方法测试
# ============================================================


class TestPluginManagerLifecycle:
    """测试 PluginManager 的 pause/resume/stop/restart/enable/disable"""

    def setup_method(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        plugin_path = os.path.join(self.tmpdir.name, "life_plugin.py")
        with open(plugin_path, "w") as f:
            f.write(
                "__plugin_name__ = 'LifePlugin'\n"
                "__plugin_version__ = '1.0.0'\n"
                "__plugin_description__ = 'Test lifecycle'\n"
            )
        self.manager = PluginManager(plugin_dir=self.tmpdir.name)

    def teardown_method(self):
        self.tmpdir.cleanup()

    def test_pause_loaded_plugin(self):
        """暂停已加载插件"""
        info = self.manager.load_plugin("life_plugin")
        assert info.state == PluginState.LOADED
        result = self.manager.pause_plugin("life_plugin")
        assert result is True
        assert info.state == PluginState.PAUSED

    def test_pause_unloaded_fails(self):
        """暂停未加载插件失败"""
        result = self.manager.pause_plugin("nonexistent")
        assert result is False

    def test_resume_paused_plugin(self):
        """恢复已暂停插件"""
        self.manager.load_plugin("life_plugin")
        self.manager.pause_plugin("life_plugin")
        result = self.manager.resume_plugin("life_plugin")
        assert result is True
        info = self.manager.get_plugin("life_plugin")
        assert info.state == PluginState.LOADED

    def test_resume_non_paused_fails(self):
        """恢复非暂停状态的插件失败"""
        self.manager.load_plugin("life_plugin")
        result = self.manager.resume_plugin("life_plugin")  # LOADED not PAUSED
        assert result is False

    def test_stop_loaded_plugin(self):
        """停止已加载插件"""
        self.manager.load_plugin("life_plugin")
        result = self.manager.stop_plugin("life_plugin")
        assert result is True
        info = self.manager.get_plugin("life_plugin")
        assert info.state == PluginState.STOPPED

    def test_stop_paused_plugin(self):
        """停止暂停中的插件"""
        self.manager.load_plugin("life_plugin")
        self.manager.pause_plugin("life_plugin")
        result = self.manager.stop_plugin("life_plugin")
        assert result is True
        info = self.manager.get_plugin("life_plugin")
        assert info.state == PluginState.STOPPED

    def test_stop_error_plugin(self):
        """停止错误状态的插件"""
        # Force error by loading from non-existent dir
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = PluginManager(plugin_dir=tmpdir)
            mgr._plugins["bad"] = PluginInfo(
                plugin_id="bad", name="bad", state=PluginState.ERROR
            )
            result = mgr.stop_plugin("bad")
            assert result is True
            assert mgr._plugins["bad"].state == PluginState.STOPPED

    def test_restart_stopped_plugin(self):
        """重载已停止的插件"""
        self.manager.load_plugin("life_plugin")
        self.manager.stop_plugin("life_plugin")
        info = self.manager.restart_plugin("life_plugin")
        assert info is not None
        assert info.state == PluginState.LOADED

    def test_restart_nonexistent(self):
        """重启不存在的插件返回 None"""
        result = self.manager.restart_plugin("nonexistent")
        assert result is None

    def test_disable_loaded_plugin(self):
        """禁用已加载插件"""
        self.manager.load_plugin("life_plugin")
        result = self.manager.disable_plugin("life_plugin")
        assert result is True
        info = self.manager.get_plugin("life_plugin")
        assert info.state == PluginState.DISABLED

    def test_disable_unloaded_plugin(self):
        """禁用未加载插件"""
        self.manager.load_plugin("life_plugin")
        self.manager.stop_plugin("life_plugin")
        result = self.manager.disable_plugin("life_plugin")
        assert result is True
        info = self.manager.get_plugin("life_plugin")
        assert info.state == PluginState.DISABLED

    def test_enable_disabled_plugin(self):
        """启用被禁用的插件"""
        self.manager.load_plugin("life_plugin")
        self.manager.disable_plugin("life_plugin")
        result = self.manager.enable_plugin("life_plugin")
        assert result is True
        info = self.manager.get_plugin("life_plugin")
        assert info.state == PluginState.UNLOADED

    def test_enable_non_disabled_fails(self):
        """启用非禁用状态的插件失败"""
        self.manager.load_plugin("life_plugin")
        result = self.manager.enable_plugin("life_plugin")
        assert result is False

    def test_get_stats_includes_new_states(self):
        """统计信息包含新状态"""
        self.manager.load_plugin("life_plugin")
        self.manager.pause_plugin("life_plugin")
        stats = self.manager.get_stats()
        assert stats["total"] == 1
        assert stats["by_state"].get("paused") == 1


# ============================================================
# 健康检查 API 测试（Manager 级别）
# ============================================================


class TestPluginHealthCheckManager:
    """测试 PluginManager 健康检查功能"""

    def setup_method(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        plugin_path = os.path.join(self.tmpdir.name, "health_plugin.py")
        with open(plugin_path, "w") as f:
            f.write(
                "__plugin_name__ = 'HealthPlugin'\n"
                "def health_check():\n"
                "    return True\n"
            )
        self.manager = PluginManager(plugin_dir=self.tmpdir.name)

    def teardown_method(self):
        self.tmpdir.cleanup()

    def test_health_check_single(self):
        """单个插件健康检查"""
        self.manager.load_plugin("health_plugin")
        result = self.manager.health_check("health_plugin")
        assert result is not None
        assert result["plugin_id"] == "health_plugin"
        assert "healthy" in result

    def test_health_check_nonexistent(self):
        """不存在的插件返回 None"""
        result = self.manager.health_check("nonexistent")
        assert result is None

    def test_health_check_all(self):
        """批量健康检查"""
        self.manager.load_plugin("health_plugin")
        results = asyncio.run(self.manager.health_check_all())
        assert len(results) == 1
        assert results[0]["plugin_id"] == "health_plugin"
