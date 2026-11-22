"""Small, side-effect-free outbound endpoint policy.

The policy validates the URL shape and any addresses already resolved by the
caller.  It deliberately does not perform DNS lookups itself: a connection
layer must resolve the hostname immediately before connecting and pass those
addresses here, so DNS rebinding cannot turn a previously accepted host into a
private destination between validation and the request.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from urllib.parse import SplitResult, urlsplit


class EndpointPolicyError(ValueError):
    """The model endpoint is outside the configured outbound boundary."""


@dataclass(frozen=True)
class ValidatedEndpoint:
    raw: str
    scheme: str
    host: str
    port: int
    path: str


_BLOCKED_HOSTNAMES = {
    "localhost",
    "localhost.localdomain",
    "ip6-localhost",
    "metadata.google.internal",
}


def _is_blocked_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError as exc:
        raise EndpointPolicyError("endpoint_address_invalid") from exc
    return bool(
        address.is_loopback
        or address.is_private
        or address.is_link_local
        or address.is_reserved
        or address.is_unspecified
        or address in ipaddress.ip_network("169.254.169.254/32")
    )


def _split(value: str) -> SplitResult:
    parsed = urlsplit(value)
    if parsed.scheme != "https":
        raise EndpointPolicyError("endpoint_https_required")
    if not parsed.hostname:
        raise EndpointPolicyError("endpoint_host_required")
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
    """Validate an HTTPS endpoint without making a network request.

    ``resolved_addresses`` is supplied by the request layer after resolving a
    hostname.  Omitting it is safe for public hostnames in configuration
    tests, while a caller that connects to an external service should always
    pass the addresses it is about to use.
    """

    parsed = _split(value.strip())
    host = parsed.hostname.lower().rstrip(".")
    try:
        host_address = ipaddress.ip_address(host)
    except ValueError:
        host_address = None
    if host in _BLOCKED_HOSTNAMES and not allow_local:
        raise EndpointPolicyError("endpoint_private_host_blocked")
    if host_address is not None and _is_blocked_address(host) and not allow_local:
        raise EndpointPolicyError("endpoint_private_address_blocked")
    if allowed_hosts is not None and host not in {item.lower().rstrip(".") for item in allowed_hosts}:
        raise EndpointPolicyError("endpoint_host_not_allowed")
    if resolved_addresses:
        for address in resolved_addresses:
            if _is_blocked_address(address) and not allow_local:
                raise EndpointPolicyError("endpoint_resolved_address_blocked")
    port = parsed.port or 443
    return ValidatedEndpoint(
        raw=value,
        scheme=parsed.scheme,
        host=host,
        port=port,
        path=parsed.path or "/",
    )


__all__ = ["EndpointPolicyError", "ValidatedEndpoint", "validate_model_endpoint"]
