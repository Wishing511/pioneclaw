"""
SSRF 防护测试

借鉴 OpenClaw ssrf.test.ts 的测试场景
"""

import pytest

from app.core.ssrf_protection import (
    SsrFBlockedError,
    SsrFPolicy,
    _is_blocked_hostname,
    _is_private_ip,
    is_blocked_hostname_or_ip,
    validate_hostname_ssrf,
    validate_url_ssrf,
    validate_url_ssrf_or_none,
)


class TestBlockedHostnames:
    """借鉴 OpenClaw BLOCKED_HOSTNAMES 测试"""

    def test_localhost(self):
        assert _is_blocked_hostname("localhost") is True
        assert _is_blocked_hostname("LOCALHOST") is True
        assert _is_blocked_hostname("localhost.localdomain") is True

    def test_special_hostnames(self):
        assert _is_blocked_hostname("metadata.google.internal") is True
        assert _is_blocked_hostname("169.254.169.254") is True

    def test_localhost_suffixes(self):
        assert _is_blocked_hostname("foo.localhost") is True
        assert _is_blocked_hostname("bar.local") is True
        assert _is_blocked_hostname("baz.internal") is True
        assert _is_blocked_hostname("api.internal") is True

    def test_normal_hostnames_ok(self):
        assert _is_blocked_hostname("example.com") is False
        assert _is_blocked_hostname("api.github.com") is False
        assert _is_blocked_hostname("google.com") is False


class TestPrivateIpAddress:
    """借鉴 OpenClaw isPrivateIpAddress 测试"""

    def test_loopback_v4(self):
        assert _is_private_ip("127.0.0.1") is True
        assert _is_private_ip("127.0.1.1") is True
        assert _is_private_ip("127.255.255.255") is True

    def test_loopback_v6(self):
        assert _is_private_ip("::1") is True
        assert _is_private_ip("0:0:0:0:0:0:0:1") is True

    def test_private_v4(self):
        assert _is_private_ip("10.0.0.1") is True
        assert _is_private_ip("172.16.0.1") is True
        assert _is_private_ip("192.168.1.1") is True

    def test_unspecified(self):
        assert _is_private_ip("0.0.0.0") is True
        assert _is_private_ip("::") is True

    def test_link_local_v4(self):
        assert _is_private_ip("169.254.1.1") is True

    def test_link_local_v6(self):
        assert _is_private_ip("fe80::1") is True

    def test_multicast(self):
        assert _is_private_ip("224.0.0.1") is True
        assert _is_private_ip("ff02::1") is True

    def test_reserved_v4(self):
        assert _is_private_ip("240.0.0.1") is True

    def test_public_ips_ok(self):
        assert _is_private_ip("8.8.8.8") is False
        assert _is_private_ip("1.1.1.1") is False
        assert _is_private_ip("208.67.222.222") is False  # OpenDNS
        assert _is_private_ip("114.114.114.114") is False  # 国内公共 DNS

    def test_rfc2544_benchmark(self):
        assert _is_private_ip("198.18.0.1") is True
        policy = SsrFPolicy(allow_rfc2544_benchmark=True)
        assert _is_private_ip("198.18.0.1", policy) is False

    def test_ipv6_ula(self):
        assert _is_private_ip("fc00::1") is True
        policy = SsrFPolicy(allow_ipv6_unique_local=True)
        assert _is_private_ip("fc00::1", policy) is False

    def test_ipv4_mapped_ipv6(self):
        assert _is_private_ip("::ffff:127.0.0.1") is True
        assert _is_private_ip("::ffff:192.168.1.1") is True
        assert _is_private_ip("::ffff:8.8.8.8") is False

    def test_allow_private_network_policy(self):
        policy = SsrFPolicy(allow_private_network=True)
        assert _is_private_ip("10.0.0.1", policy) is False
        assert _is_private_ip("172.16.0.1", policy) is False
        assert _is_private_ip("192.168.1.1", policy) is False
        # loopback 仍然阻止
        assert _is_private_ip("127.0.0.1", policy) is True

    def test_dangerously_allow_private_network(self):
        policy = SsrFPolicy(dangerously_allow_private_network=True)
        assert _is_private_ip("127.0.0.1", policy) is False
        assert _is_private_ip("10.0.0.1", policy) is False
        assert _is_private_ip("::1", policy) is False


class TestIsBlockedHostnameOrIp:
    """借鉴 OpenClaw isBlockedHostnameOrIp 测试"""

    def test_blocked_hostname(self):
        assert is_blocked_hostname_or_ip("localhost") is True
        assert is_blocked_hostname_or_ip("foo.localhost") is True

    def test_blocked_ip(self):
        assert is_blocked_hostname_or_ip("127.0.0.1") is True
        assert is_blocked_hostname_or_ip("10.0.0.1") is True

    def test_public_ok(self):
        assert is_blocked_hostname_or_ip("example.com") is False
        assert is_blocked_hostname_or_ip("8.8.8.8") is False

    def test_malformed_ip_blocked(self):
        # 看起来像 IP 但无法解析，安全起见阻止
        assert is_blocked_hostname_or_ip("0.0") is True

    def test_empty_ok(self):
        assert is_blocked_hostname_or_ip("") is False


class TestValidateHostnameSsrF:
    """借鉴 OpenClaw assertHostnameAllowedWithPolicy 测试"""

    def test_normal_hostname(self):
        result = validate_hostname_ssrf("example.com")
        assert result == "example.com"

    def test_blocked_hostname_raises(self):
        with pytest.raises(SsrFBlockedError):
            validate_hostname_ssrf("localhost")

    def test_blocked_ip_raises(self):
        with pytest.raises(SsrFBlockedError):
            validate_hostname_ssrf("127.0.0.1")

    def test_allowlist_exact_match(self):
        # hostname_allowlist 是额外限制：只有列表中的 hostname 才放行
        policy = SsrFPolicy(hostname_allowlist=["example.com"])
        result = validate_hostname_ssrf("example.com", policy)
        assert result == "example.com"

        # 不在白名单的被阻止
        with pytest.raises(SsrFBlockedError):
            validate_hostname_ssrf("other.com", policy)

    def test_allowlist_wildcard(self):
        policy = SsrFPolicy(hostname_allowlist=["*.example.com"])
        result = validate_hostname_ssrf("api.example.com", policy)
        assert result == "api.example.com"

        # 根域名不在通配符范围内
        with pytest.raises(SsrFBlockedError):
            validate_hostname_ssrf("example.com", policy)

    def test_allowed_hostnames_skip_private_check(self):
        policy = SsrFPolicy(allowed_hostnames=["privateservice.local"])
        # 即使 *.local 通常被阻止，白名单中的 hostname 跳过检查
        result = validate_hostname_ssrf("privateservice.local", policy)
        assert result == "privateservice.local"


class TestValidateUrlSsrF:
    """借鉴 OpenClaw fetchWithSsrFGuard URL 检查测试"""

    def test_normal_url(self):
        result = validate_url_ssrf("https://example.com/api/data")
        assert result == "example.com"

    def test_http_ok(self):
        result = validate_url_ssrf("http://example.com")
        assert result == "example.com"

    def test_blocked_scheme(self):
        with pytest.raises(SsrFBlockedError):
            validate_url_ssrf("ftp://example.com")

    def test_blocked_hostname_url(self):
        with pytest.raises(SsrFBlockedError):
            validate_url_ssrf("http://localhost:8080/api")

    def test_blocked_private_ip_url(self):
        with pytest.raises(SsrFBlockedError):
            validate_url_ssrf("http://192.168.1.1/admin")

    def test_blocked_metadata_url(self):
        with pytest.raises(SsrFBlockedError):
            validate_url_ssrf("http://169.254.169.254/latest/meta-data/")

    def test_blocked_loopback_url(self):
        with pytest.raises(SsrFBlockedError):
            validate_url_ssrf("http://127.0.0.1:6379/")

    def test_invalid_url(self):
        with pytest.raises(SsrFBlockedError):
            validate_url_ssrf("not-a-url")

    def test_missing_hostname(self):
        with pytest.raises(SsrFBlockedError):
            validate_url_ssrf("http:///path")

    def test_valid_url_or_none(self):
        assert validate_url_ssrf_or_none("https://example.com") is None
        error = validate_url_ssrf_or_none("http://127.0.0.1:6379/")
        assert error is not None
        assert "Blocked" in error


class TestUrlWithPorts:
    def test_url_with_port(self):
        result = validate_url_ssrf("https://example.com:8443/api")
        assert result == "example.com"

    def test_blocked_url_with_port(self):
        with pytest.raises(SsrFBlockedError):
            validate_url_ssrf("http://127.0.0.1:3000/api")


class TestSsrFPolicyEquivalence:
    def test_default_policy(self):
        p = SsrFPolicy()
        assert p.allow_private_network is False
        assert p.dangerously_allow_private_network is False

    def test_lenient_policy(self):
        p = SsrFPolicy(
            allow_private_network=True,
            allowed_hostnames=["internal.local"],
            hostname_allowlist=["*.corp.com"],
        )
        assert p.allow_private_network is True
        assert "internal.local" in p.allowed_hostnames
        assert "*.corp.com" in p.hostname_allowlist
