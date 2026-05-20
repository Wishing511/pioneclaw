"""运行时配置获取

消除 api.config 和 engines.model_engine 之间的循环导入。
"""


def get_runtime_config() -> dict:
    """获取运行时配置（如果 api.config 已加载）"""
    try:
        from api.config import get_runtime_config as _get
        return _get()
    except Exception:
        return {}
