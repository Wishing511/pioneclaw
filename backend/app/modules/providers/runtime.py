"""
Provider 运行时 - Key 轮换和配置覆盖

借鉴自 CountBot 的 runtime.py
"""

import threading
from dataclasses import dataclass, field


def _normalized_text(value: str | None) -> str | None:
    """规范化文本"""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


@dataclass
class ProviderRuntimeState:
    """Provider 当前运行态状态"""

    provider_id: str
    exists: bool
    enabled: bool
    configured: bool
    selectable: bool
    requires_api_key: bool
    requires_api_base: bool
    api_key: str
    api_keys: list[str] = field(default_factory=list)
    api_base: str | None = None
    status: str = "disabled"
    reason: str = "disabled"


class KeyRotator:
    """线程安全的 API Key 轮换器，支持轮询和故障转移"""

    def __init__(self, api_keys: list[str]):
        self._keys = [k for k in api_keys if k and k.strip()]
        self._index = 0
        self._lock = threading.Lock()

    @property
    def keys(self) -> list[str]:
        return list(self._keys)

    @property
    def count(self) -> int:
        return len(self._keys)

    def next_key(self) -> str | None:
        """获取下一个 key（轮询策略）"""
        with self._lock:
            if not self._keys:
                return None
            key = self._keys[self._index % len(self._keys)]
            self._index = (self._index + 1) % len(self._keys)
            return key

    def current_key(self) -> str | None:
        """获取当前 key（不移动指针）"""
        with self._lock:
            if not self._keys:
                return None
            return self._keys[self._index % len(self._keys)]

    def mark_key_failed(self, failed_key: str) -> str | None:
        """标记某个 key 失败，返回下一个可用 key"""
        with self._lock:
            if len(self._keys) <= 1:
                return None
            try:
                idx = self._keys.index(failed_key)
            except ValueError:
                return self.next_key()
            next_idx = (idx + 1) % len(self._keys)
            if self._keys[next_idx] == failed_key:
                return None
            self._index = next_idx
            return self._keys[self._index]

    def is_auth_error(self, error: Exception) -> bool:
        """判断错误是否为认证/密钥相关错误"""
        error_text = f"{type(error).__name__} {str(error)}".lower()
        auth_hints = (
            "401",
            "unauthorized",
            "invalid api key",
            "invalid_api_key",
            "authentication",
            "invalid token",
            "token is unusable",
            "api key",
            "apikey",
            "access denied",
            "forbidden",
            "insufficient_quota",
            "account_deactivated",
        )
        return any(hint in error_text for hint in auth_hints)

    def is_rate_limit_error(self, error: Exception) -> bool:
        """判断错误是否为限流/配额错误"""
        error_text = f"{type(error).__name__} {str(error)}".lower()
        rate_hints = (
            "429",
            "rate limit",
            "rate_limit",
            "quota",
            "too many requests",
            "insufficient_quota",
            "capacity",
            "overloaded",
        )
        return any(hint in error_text for hint in rate_hints)

    def should_rotate_key(self, error: Exception) -> bool:
        """判断是否应该轮换到下一个 key"""
        return self.is_auth_error(error) or self.is_rate_limit_error(error)


# 全局 KeyRotator 缓存
_PROVIDER_KEY_ROTATORS: dict[str, KeyRotator] = {}
_ROTATOR_LOCK = threading.Lock()


def get_key_rotator(provider_id: str, api_keys: list[str]) -> KeyRotator:
    """获取或更新指定 provider 的 KeyRotator 实例"""
    with _ROTATOR_LOCK:
        existing = _PROVIDER_KEY_ROTATORS.get(provider_id)
        if existing is not None and existing.keys == api_keys:
            return existing
        rotator = KeyRotator(api_keys)
        _PROVIDER_KEY_ROTATORS[provider_id] = rotator
        return rotator


def clear_key_rotator(provider_id: str) -> None:
    """清除指定 provider 的 KeyRotator 缓存"""
    with _ROTATOR_LOCK:
        _PROVIDER_KEY_ROTATORS.pop(provider_id, None)


def clear_all_key_rotators() -> None:
    """清除所有 KeyRotator 缓存"""
    with _ROTATOR_LOCK:
        _PROVIDER_KEY_ROTATORS.clear()


@dataclass
class ModelOverride:
    """模型覆盖配置（会话级）"""

    provider: str | None = None
    model: str | None = None
    api_key: str | None = None
    api_keys: list[str] | None = None
    api_base: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    max_iterations: int | None = None

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "model": self.model,
            "api_key": self.api_key,
            "api_keys": self.api_keys,
            "api_base": self.api_base,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "max_iterations": self.max_iterations,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ModelOverride":
        return cls(
            provider=data.get("provider"),
            model=data.get("model"),
            api_key=data.get("api_key"),
            api_keys=data.get("api_keys"),
            api_base=data.get("api_base"),
            temperature=data.get("temperature"),
            max_tokens=data.get("max_tokens"),
            max_iterations=data.get("max_iterations"),
        )


@dataclass
class RuntimeConfig:
    """运行时配置（包含 Provider、Key 轮换、模型覆盖）"""

    provider_id: str
    model: str
    api_keys: list[str] = field(default_factory=list)
    api_base: str | None = None
    temperature: float = 0.7
    max_tokens: int = 4096

    # Key 轮换
    key_rotator: KeyRotator | None = None

    # 模型覆盖
    model_override: ModelOverride | None = None

    # Thinking
    thinking_enabled: bool = False
    thinking_budget: int = 10000

    def __post_init__(self):
        if self.api_keys and not self.key_rotator:
            self.key_rotator = KeyRotator(self.api_keys)

    def get_api_key(self) -> str | None:
        """获取 API Key"""
        if self.model_override and self.model_override.api_key:
            return self.model_override.api_key
        if self.key_rotator:
            return self.key_rotator.next_key()
        return self.api_keys[0] if self.api_keys else None

    def get_model(self) -> str:
        """获取模型名称"""
        if self.model_override and self.model_override.model:
            return self.model_override.model
        return self.model

    def get_temperature(self) -> float:
        """获取温度"""
        if self.model_override and self.model_override.temperature is not None:
            return self.model_override.temperature
        return self.temperature

    def get_max_tokens(self) -> int:
        """获取最大 token"""
        if self.model_override and self.model_override.max_tokens is not None:
            return self.model_override.max_tokens
        return self.max_tokens
