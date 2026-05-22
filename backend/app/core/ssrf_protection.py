"""
SSRF (Server-Side Request Forgery) 防护

借鉴 OpenClaw src/infra/net/ssrf.ts + fetch-guard.ts

核心思路（两层检查）：
Layer 1 (Pre-DNS): 在 DNS 解析之前检查 hostname 是否为已知内部地址
  - 阻止特殊 hostname（localhost, *.local, *.internal, metadata.google.internal）
  - 阻止字面量私有/特殊 IP（127.0.0.1, 10.x, 172.16-31.x, 192.168.x, ::1, fe80:: 等）

Layer 2 (Post-DNS): DNS 解析后检查解析结果（防止 DNS rebinding）
  - 暂不实现（PioneClaw 使用 httpx，DNS 由系统 resolver 处理）

使用示例:
    from app.core.ssrf_protection import validate_url_ssrf, SsrFPolicy, SsrFBlockedError

    policy = SsrFPolicy(allow_private_network=False)
    try:
        validate_url_ssrf("https://example.com/api", policy)
    except SsrFBlockedError as e:
        print(f"Blocked: {e}")
"""

import ipaddress
import logging
from dataclasses import dataclass, field
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


class SsrFBlockedError(ValueError):
    """SSRF 阻止错误 —— 借鉴 OpenClaw SsrFBlockedError"""

    def __init__(self, message: str):
        super().__init__(message)
        self.name = "SsrFBlockedError"


# 借鉴 OpenClaw BLOCKED_HOSTNAMES
_BLOCKED_HOSTNAMES = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "metadata.google.internal",  # GCP 元数据端点
        "169.254.169.254",  # AWS/云 元数据端点（IP 字面量也阻止）
    }
)

# 借鉴 OpenClaw isBlockedHostnameNormalized: *.localhost / *.local / *.internal
_BLOCKED_HOSTNAME_SUFFIXES = (".localhost", ".local", ".internal")


@dataclass
class SsrFPolicy:
    """SSRF 策略 —— 借鉴 OpenClaw SsrFPolicy

    Attributes:
        allow_private_network: 允许访问私有网络（10.x, 172.16-31.x, 192.168.x, fc00::/7）
        dangerously_allow_private_network: 完全允许私有网络（包括 loopback/link-local）
        allow_rfc2544_benchmark: 允许 198.18.0.0/15 测试网段（用于代理/假 IP 场景）
        allow_ipv6_unique_local: 允许 fc00::/7（ULA 地址，用于代理栈场景）
        allowed_hostnames: 白名单 hostname 列表（这些 hostname 跳过私有网络检查）
        hostname_allowlist: hostname 通配符白名单（支持 *.example.com 格式）
    """

    allow_private_network: bool = False
    dangerously_allow_private_network: bool = False
    allow_rfc2544_benchmark: bool = False
    allow_ipv6_unique_local: bool = False
    allowed_hostnames: list[str] = field(default_factory=list)
    hostname_allowlist: list[str] = field(default_factory=list)


def _is_private_ip(ip_str: str, policy: SsrFPolicy | None = None) -> bool:
    """检查 IP 地址是否为私有/特殊用途地址 —— 借鉴 OpenClaw isPrivateIpAddress

    Python 的 ipaddress 模块覆盖了 OpenClaw isBlockedSpecialUseIpv4Address +
    isBlockedSpecialUseIpv6Address 的逻辑。

    Args:
        ip_str: IP 地址字符串（IPv4 或 IPv6）
        policy: SSRF 策略，控制哪些网段可以豁免

    Returns:
        True 如果应该被阻止
    """
    try:
        addr = ipaddress.ip_address(ip_str)
    except ValueError:
        return False

    p = policy or SsrFPolicy()

    # 检查 IPv4
    if isinstance(addr, ipaddress.IPv4Address):
        return _is_blocked_ipv4(addr, p)

    # 检查 IPv6
    if isinstance(addr, ipaddress.IPv6Address):
        return _is_blocked_ipv6(addr, p)

    return False


def _is_blocked_ipv4(addr: ipaddress.IPv4Address, policy: SsrFPolicy) -> bool:
    """检查 IPv4 地址是否应被阻止 —— 借鉴 OpenClaw isBlockedSpecialUseIpv4Address"""
    # dangerously_allow_private 允许一切
    if policy.dangerously_allow_private_network:
        return False

    # 永远阻止未指定 (0.0.0.0)
    if addr.is_unspecified:
        return True

    # Loopback: 127.0.0.0/8
    if addr.is_loopback:
        return True

    # Link-local: 169.254.0.0/16
    if addr.is_link_local:
        return True

    # RFC 2544 基准测试: 198.18.0.0/15（Python 3.13+ 归入 is_private/is_reserved）
    in_rfc2544 = 0xC6120000 <= int(addr) <= 0xC613FFFF  # 198.18.0.0 - 198.19.255.255
    if in_rfc2544:
        if not policy.allow_rfc2544_benchmark:
            return True
        # 允许此段，跳过后续所有检查
        return False

    # 私有: 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16
    if addr.is_private and not policy.allow_private_network:
        return True

    # 组播: 224.0.0.0/4
    if addr.is_multicast:
        return True

    # 保留: 240.0.0.0/4
    return bool(addr.is_reserved)


def _is_blocked_ipv6(addr: ipaddress.IPv6Address, policy: SsrFPolicy) -> bool:
    """检查 IPv6 地址是否应被阻止 —— 借鉴 OpenClaw isBlockedSpecialUseIpv6Address"""
    # dangerously_allow_private 允许一切
    if policy.dangerously_allow_private_network:
        return False

    # 永远阻止未指定 (::)
    if addr.is_unspecified:
        return True

    # Loopback: ::1
    if addr.is_loopback:
        return True

    # Link-local: fe80::/10
    if addr.is_link_local:
        return True

    # 组播: ff00::/8
    if addr.is_multicast:
        return True

    # 私有 (Unique Local): fc00::/7
    if addr.is_private and not (
        policy.allow_private_network or policy.allow_ipv6_unique_local
    ):
        return True

    # 检查嵌入的 IPv4 地址 —— 借鉴 OpenClaw extractEmbeddedIpv4FromIpv6
    if addr.ipv4_mapped is not None:
        return _is_blocked_ipv4(addr.ipv4_mapped, policy)

    # 检查 6to4、Teredo 等（包含在 ipaddress 的 is_reserved 中）
    return bool(addr.is_reserved)


def _is_blocked_hostname(hostname: str) -> bool:
    """检查 hostname 是否为已知内部地址 —— 借鉴 OpenClaw isBlockedHostname

    阻止:
    - localhost
    - localhost.localdomain
    - metadata.google.internal
    - 169.254.169.254
    - *.localhost
    - *.local
    - *.internal
    """
    normalized = hostname.lower().strip().rstrip(".")
    if not normalized:
        return False

    if normalized in _BLOCKED_HOSTNAMES:
        return True

    # 借鉴 OpenClaw: normalized.endsWith(".localhost") / ".local" / ".internal"
    return normalized.endswith(_BLOCKED_HOSTNAME_SUFFIXES)


def _matches_hostname_allowlist(hostname: str, allowlist: list[str]) -> bool:
    """检查 hostname 是否匹配通配符白名单 —— 借鉴 OpenClaw matchesHostnameAllowlist

    支持:
    - exact: "example.com" 精确匹配
    - wildcard: "*.example.com" 匹配 example.com 的所有子域名
    """
    if not allowlist:
        return True  # 空白名单 = 全部允许

    normalized = hostname.lower().strip().rstrip(".")
    for pattern in allowlist:
        pattern_normalized = pattern.lower().strip().rstrip(".")
        if pattern_normalized.startswith("*."):
            suffix = pattern_normalized[2:]
            if suffix and normalized.endswith("." + suffix):
                return True
        elif normalized == pattern_normalized:
            return True
    return False


def _is_private_network_allowed(policy: SsrFPolicy | None) -> bool:
    """是否允许私有网络 —— 借鉴 OpenClaw isPrivateNetworkAllowedByPolicy"""
    if policy is None:
        return False
    return policy.dangerously_allow_private_network or policy.allow_private_network


def _should_skip_private_checks(hostname: str, policy: SsrFPolicy | None) -> bool:
    """是否跳过私有网络检查 —— 借鉴 OpenClaw shouldSkipPrivateNetworkChecks

    如果 hostname 在 allowed_hostnames 中，则跳过私有 IP 检查
    （允许访问该 hostname 解析到的任何 IP）
    """
    if _is_private_network_allowed(policy):
        return True
    return bool(
        policy and hostname.lower() in {h.lower() for h in policy.allowed_hostnames}
    )


def _looks_like_ipv4_literal(addr: str) -> bool:
    """检查字符串是否看起来像 IPv4 字面量 —— 借鉴 OpenClaw looksLikeUnsupportedIpv4Literal"""
    parts = addr.split(".")
    if not parts or len(parts) > 4:
        return False
    if any(p == "" for p in parts):
        return True
    # 所有部分都是数字或 0x 前缀
    return all(p.isdigit() or p.lower().startswith("0x") for p in parts)


def is_blocked_hostname_or_ip(hostname: str, policy: SsrFPolicy | None = None) -> bool:
    """检查 hostname 或 IP 是否应被阻止 —— 借鉴 OpenClaw isBlockedHostnameOrIp

    这是 SSRF 检查的核心入口函数。

    Args:
        hostname: 主机名或 IP 地址字符串
        policy: SSRF 策略

    Returns:
        True 如果应该被阻止
    """
    normalized = hostname.lower().strip().rstrip(".")
    if not normalized:
        return False

    # 检查特例 hostname
    if _is_blocked_hostname(normalized):
        return True

    # 检查是否为被阻止的 IP 地址
    if _is_private_ip(normalized, policy):
        return True

    # 检查看起来像 IPv4 字面量但无法解析的（如 "0.0" 这种）
    if not _is_blocked_hostname(normalized) and _looks_like_ipv4_literal(normalized):
        try:
            ipaddress.ip_address(normalized)
        except ValueError:
            return True  # 无法解析的 IP 格式，安全起见阻止

    return False


def validate_hostname_ssrf(hostname: str, policy: SsrFPolicy | None = None) -> str:
    """验证 hostname 是否安全，不通过则抛异常 —— 借鉴 OpenClaw assertHostnameAllowedWithPolicy

    Returns:
        规范化后的 hostname

    Raises:
        SsrFBlockedError: hostname 被 SSRF 策略阻止
    """
    normalized = hostname.lower().strip().rstrip(".")
    if not normalized:
        raise SsrFBlockedError("Empty hostname")

    # 检查 hostname 白名单
    if policy and policy.hostname_allowlist:
        if not _matches_hostname_allowlist(normalized, policy.hostname_allowlist):
            raise SsrFBlockedError(f"Blocked hostname (not in allowlist): {hostname}")

    # 如果跳过私有检查（白名单中的 hostname），直接返回
    if _should_skip_private_checks(normalized, policy):
        return normalized

    if is_blocked_hostname_or_ip(normalized, policy):
        raise SsrFBlockedError(
            f"Blocked hostname or private/internal/special-use IP address: {hostname}"
        )

    return normalized


def validate_url_ssrf(url: str, policy: SsrFPolicy | None = None) -> str:
    """验证 URL 是否安全（SSRF 防护） —— 借鉴 OpenClaw fetchWithSsrFGuard 中的检查逻辑

    Args:
        url: 完整的 URL 字符串
        policy: SSRF 策略

    Returns:
        规范化后的 hostname（如果通过验证）

    Raises:
        SsrFBlockedError: URL 被 SSRF 策略阻止
    """
    # 解析 URL
    try:
        parsed = urlparse(url)
    except Exception:
        raise SsrFBlockedError(f"Invalid URL: {url}")

    # 只允许 http/https
    if parsed.scheme not in ("http", "https"):
        raise SsrFBlockedError(f"Only http/https URLs allowed, got: {parsed.scheme}")

    # 必须有 hostname
    if not parsed.hostname:
        raise SsrFBlockedError(f"Missing hostname in URL: {url}")

    return validate_hostname_ssrf(parsed.hostname, policy)


def validate_url_ssrf_or_none(url: str, policy: SsrFPolicy | None = None) -> str | None:
    """便捷方法：验证 URL，失败返回 SsrFBlockedError 的字符串消息而非抛异常

    适应 PioneClaw 工具 execute() 返回字符串错误消息的模式。
    """
    try:
        validate_url_ssrf(url, policy)
        return None
    except SsrFBlockedError as e:
        return str(e)
