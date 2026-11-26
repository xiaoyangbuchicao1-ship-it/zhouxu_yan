"""Outbound endpoint validation and DNS-pinning transport for model requests."""

from __future__ import annotations

import asyncio
import ipaddress
import os
import socket
import ssl
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from urllib.parse import SplitResult, urlsplit

import httpcore
import httpx
from httpcore._backends.auto import AutoBackend


class EndpointPolicyError(ValueError):
    """The model endpoint is outside the configured outbound boundary."""


@dataclass(frozen=True)
class ValidatedEndpoint:
    raw: str
    scheme: str
    host: str
    port: int
    path: str


_LOCAL_HOSTNAMES = frozenset({
    "localhost",
    "localhost.localdomain",
    "ip6-localhost",
    "host.docker.internal",
})
_METADATA_HOSTNAMES = frozenset({
    "metadata.google.internal",
    "metadata.goog",
})
# RFC 2544 benchmarking range used by Clash/Mihomo/sing-box TUN Fake-IP DNS proxies.
_TUN_FAKE_IP_NETWORK = ipaddress.ip_network("198.18.0.0/15")


def local_model_hosts_enabled() -> bool:
    return os.environ.get("ZHOUXU_ALLOW_LOCAL_MODEL_HOSTS", "").strip() == "1"


def fake_ip_proxy_enabled() -> bool:
    return os.environ.get("ZHOUXU_ALLOW_FAKE_IP_PROXY", "1").strip() != "0"


def _is_tun_fake_ip(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return _is_tun_fake_ip(address.ipv4_mapped)
    return isinstance(address, ipaddress.IPv4Address) and address in _TUN_FAKE_IP_NETWORK


def _configured_local_hosts() -> set[str]:
    raw = os.environ.get("ZHOUXU_LOCAL_MODEL_HOSTS", "")
    return {item.strip().lower().rstrip(".") for item in raw.split(",") if item.strip()}


def _is_always_blocked_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return _is_always_blocked_address(address.ipv4_mapped)
    return bool(
        address.is_link_local
        or address.is_multicast
        or address.is_unspecified
        or address in ipaddress.ip_network("169.254.169.254/32")
    )


def _is_private_or_loopback_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return _is_private_or_loopback_address(address.ipv4_mapped)
    return bool(
        address.is_loopback
        or address.is_private
        or address.is_reserved
    )


def _is_local_allowed_host(host: str) -> bool:
    normalized = host.lower().rstrip(".")
    if normalized in _LOCAL_HOSTNAMES or normalized in _configured_local_hosts():
        return True
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        return False
    return bool(
        not _is_always_blocked_address(address)
        and (address.is_loopback or address.is_private)
    )


def _parse_address(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    clean = value.split("%", 1)[0]
    try:
        return ipaddress.ip_address(clean)
    except ValueError as exc:
        raise EndpointPolicyError("endpoint_address_invalid") from exc


def validate_resolved_addresses(
    addresses: Iterable[str],
    *,
    allow_local: bool = False,
    host: str = "",
    allow_fake_ip: bool = False,
) -> list[str]:
    """Validate resolved IPs and return the clean ordered IP strings."""

    cleaned: list[str] = []
    local_host = allow_local and _is_local_allowed_host(host)
    for raw in addresses:
        address = _parse_address(raw)
        if _is_always_blocked_address(address):
            raise EndpointPolicyError("endpoint_resolved_address_blocked")
        if _is_private_or_loopback_address(address):
            if allow_fake_ip and _is_tun_fake_ip(address):
                cleaned.append(str(address))
                continue
            if not (allow_local and (local_host or address.is_loopback)):
                raise EndpointPolicyError("endpoint_resolved_address_blocked")
        cleaned.append(str(address))
    if not cleaned:
        raise EndpointPolicyError("endpoint_address_invalid")
    return cleaned


def _split(value: str, *, allow_local: bool = False) -> SplitResult:
    parsed = urlsplit(value)
    if not parsed.hostname:
        raise EndpointPolicyError("endpoint_host_required")
    host = parsed.hostname.lower().rstrip(".")
    if parsed.scheme == "http":
        if not (allow_local and _is_local_allowed_host(host)):
            raise EndpointPolicyError("endpoint_https_required")
    elif parsed.scheme != "https":
        raise EndpointPolicyError("endpoint_https_required")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise EndpointPolicyError("endpoint_url_contains_sensitive_parts")
    try:
        _ = parsed.port
    except ValueError as exc:
        raise EndpointPolicyError("endpoint_port_invalid") from exc
    return parsed


def validate_model_endpoint(
    value: str,
    *,
    allowed_hosts: set[str] | None = None,
    resolved_addresses: set[str] | None = None,
    allow_local: bool = False,
) -> ValidatedEndpoint:
    """Validate an HTTPS (or opt-in local HTTP) endpoint."""

    parsed = _split(value.strip(), allow_local=allow_local)
    host = parsed.hostname.lower().rstrip(".")
    if host in _METADATA_HOSTNAMES:
        raise EndpointPolicyError("endpoint_private_host_blocked")
    try:
        host_address = ipaddress.ip_address(host)
    except ValueError:
        host_address = None
    if host in _LOCAL_HOSTNAMES and not allow_local:
        raise EndpointPolicyError("endpoint_private_host_blocked")
    if host_address is not None:
        if _is_always_blocked_address(host_address):
            raise EndpointPolicyError("endpoint_private_address_blocked")
        if _is_private_or_loopback_address(host_address) and not (
            allow_local and _is_local_allowed_host(host)
        ):
            raise EndpointPolicyError("endpoint_private_address_blocked")
    if allowed_hosts is not None and host not in {item.lower().rstrip(".") for item in allowed_hosts}:
        raise EndpointPolicyError("endpoint_host_not_allowed")
    if resolved_addresses is not None:
        validate_resolved_addresses(resolved_addresses, allow_local=allow_local, host=host)
    port = parsed.port or (80 if parsed.scheme == "http" else 443)
    return ValidatedEndpoint(
        raw=value,
        scheme=parsed.scheme,
        host=host,
        port=port,
        path=parsed.path or "/",
    )


class PinnedAsyncNetworkBackend(httpcore.AsyncNetworkBackend):
    """Resolve and validate every DNS result before opening a socket to the pinned IP."""

    def __init__(
        self,
        *,
        allow_local: bool = False,
        inner: httpcore.AsyncNetworkBackend | None = None,
        resolver: Callable[[str, int], Iterable[str]] | None = None,
    ):
        self.allow_local = allow_local
        self._inner = inner or AutoBackend()
        self._resolver = resolver

    async def _resolve(self, host: str, port: int) -> list[str]:
        if self._resolver is not None:
            return list(self._resolver(host, port))
        loop = asyncio.get_running_loop()
        infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        return list(dict.fromkeys(info[4][0] for info in infos if info[4]))

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options=None,
    ) -> httpcore.AsyncNetworkStream:
        normalized = host.lower().rstrip(".")
        if normalized in _METADATA_HOSTNAMES:
            raise EndpointPolicyError("endpoint_private_host_blocked")
        try:
            literal = _parse_address(normalized)
        except EndpointPolicyError:
            resolved = await self._resolve(normalized, port)
            validated = validate_resolved_addresses(
                resolved,
                allow_local=self.allow_local,
                host=normalized,
                allow_fake_ip=fake_ip_proxy_enabled(),
            )
            pinned_ip = validated[0]
        else:
            validate_resolved_addresses(
                [str(literal)], allow_local=self.allow_local, host=normalized
            )
            pinned_ip = str(literal)
        return await self._inner.connect_tcp(
            pinned_ip,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )

    async def connect_unix_socket(self, path: str, timeout: float | None = None, socket_options=None):
        raise EndpointPolicyError("endpoint_unix_socket_blocked")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


class PinnedAsyncHTTPTransport(httpx.AsyncHTTPTransport):
    """``httpx.AsyncHTTPTransport`` backed by :class:`PinnedAsyncNetworkBackend`."""

    def __init__(
        self,
        *,
        allow_local: bool = False,
        verify: ssl.SSLContext | str | bool = True,
        trust_env: bool = False,
        **kwargs,
    ):
        super().__init__(verify=verify, trust_env=trust_env, **kwargs)
        ssl_context = httpx._config.create_ssl_context(verify=verify, trust_env=trust_env)
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=ssl_context,
            network_backend=PinnedAsyncNetworkBackend(allow_local=allow_local),
        )


def pinned_async_transport(*, allow_local: bool | None = None) -> httpx.AsyncBaseTransport:
    enabled = local_model_hosts_enabled() if allow_local is None else allow_local
    return PinnedAsyncHTTPTransport(allow_local=enabled)


__all__ = [
    "EndpointPolicyError",
    "PinnedAsyncHTTPTransport",
    "PinnedAsyncNetworkBackend",
    "ValidatedEndpoint",
    "local_model_hosts_enabled",
    "pinned_async_transport",
    "validate_model_endpoint",
    "validate_resolved_addresses",
]
