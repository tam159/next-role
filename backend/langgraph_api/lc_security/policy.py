"""SSRF protection policy with IP validation and DNS-aware URL checking."""

import asyncio
import dataclasses
import ipaddress
import socket
import urllib.parse

import structlog

from langgraph_api.lc_security.exceptions import SSRFBlockedError

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Blocklist constants
# ---------------------------------------------------------------------------

# Ranges that are NEVER valid webhook targets (always blocked regardless of
# deployment mode).
_ALWAYS_BLOCKED_IPV4_NETWORKS: tuple[ipaddress.IPv4Network, ...] = tuple(
    ipaddress.IPv4Network(n)
    for n in (
        "169.254.0.0/16",  # RFC 3927 - link-local
        "0.0.0.0/8",  # RFC 1122 - "this network"
        "192.0.0.0/24",  # RFC 6890 - IETF protocol assignments
        "192.0.2.0/24",  # RFC 5737 - TEST-NET-1 (documentation)
        "198.18.0.0/15",  # RFC 2544 - benchmarking
        "198.51.100.0/24",  # RFC 5737 - TEST-NET-2 (documentation)
        "203.0.113.0/24",  # RFC 5737 - TEST-NET-3 (documentation)
        "224.0.0.0/4",  # RFC 5771 - multicast
        "240.0.0.0/4",  # RFC 1112 - reserved for future use
        "255.255.255.255/32",  # RFC 919  - limited broadcast
    )
)

# Loopback ranges — blocked when block_localhost is set, allowed by default.
_LOOPBACK_IPV4_NETWORKS: tuple[ipaddress.IPv4Network, ...] = tuple(
    ipaddress.IPv4Network(n)
    for n in ("127.0.0.0/8",)  # RFC 1122 - loopback
)

# Private/internal ranges — blocked in SAAS, allowed in self-hosted/BYOC
# where internal webhook targets are legitimate.
_PRIVATE_IPV4_NETWORKS: tuple[ipaddress.IPv4Network, ...] = tuple(
    ipaddress.IPv4Network(n)
    for n in (
        "10.0.0.0/8",  # RFC 1918 - private class A
        "172.16.0.0/12",  # RFC 1918 - private class B
        "192.168.0.0/16",  # RFC 1918 - private class C
        "100.64.0.0/10",  # RFC 6598 - shared/CGN address space
    )
)

_ALWAYS_BLOCKED_IPV6_NETWORKS: tuple[ipaddress.IPv6Network, ...] = tuple(
    ipaddress.IPv6Network(n)
    for n in (
        "fe80::/10",  # RFC 4291 - link-local
        "ff00::/8",  # RFC 4291 - multicast
        "::ffff:0:0/96",  # RFC 4291 - IPv4-mapped IPv6 addresses
        "::0.0.0.0/96",  # RFC 4291 - IPv4-compatible IPv6 (deprecated)
        "64:ff9b::/96",  # RFC 6052 - NAT64 well-known prefix
        "64:ff9b:1::/48",  # RFC 8215 - NAT64 discovery prefix
    )
)

_LOOPBACK_IPV6_NETWORKS: tuple[ipaddress.IPv6Network, ...] = tuple(
    ipaddress.IPv6Network(n)
    for n in ("::1/128",)  # RFC 4291 - loopback
)

# Private/internal IPv6 — blocked in SAAS, allowed in self-hosted/BYOC.
_PRIVATE_IPV6_NETWORKS: tuple[ipaddress.IPv6Network, ...] = tuple(
    ipaddress.IPv6Network(n)
    for n in (
        "fc00::/7",  # RFC 4193 - unique local addresses (ULA)
    )
)

_CLOUD_METADATA_IPS: frozenset[str] = frozenset(
    {
        "169.254.169.254",
        "169.254.170.2",
        "100.100.100.200",
        "fd00:ec2::254",
    }
)

_CLOUD_METADATA_HOSTNAMES: frozenset[str] = frozenset(
    {
        "metadata.google.internal",
        "metadata.amazonaws.com",
        "metadata",
        "instance-data",
    }
)

_LOCALHOST_NAMES: frozenset[str] = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "host.docker.internal",
    }
)

_K8S_SUFFIX = ".svc.cluster.local"

# NAT64 well-known prefixes
_NAT64_PREFIX = ipaddress.IPv6Network("64:ff9b::/96")

# ---------------------------------------------------------------------------
# SSRFPolicy
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class SSRFPolicy:
    """Immutable policy controlling which URLs/IPs are considered safe."""

    allowed_schemes: frozenset[str] = frozenset({"http", "https"})
    block_private_ips: bool = False
    block_localhost: bool = False
    block_cloud_metadata: bool = True
    block_k8s_internal: bool = True
    allowed_hosts: frozenset[str] = frozenset()
    additional_blocked_cidrs: tuple[
        ipaddress.IPv4Network | ipaddress.IPv6Network, ...
    ] = ()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_embedded_ipv4(
    addr: ipaddress.IPv6Address,
) -> ipaddress.IPv4Address | None:
    """Extract an embedded IPv4 from IPv4-mapped or NAT64 IPv6 addresses."""
    # Check ipv4_mapped first (covers ::ffff:x.x.x.x)
    if addr.ipv4_mapped is not None:
        return addr.ipv4_mapped

    # Check NAT64 well-known prefix (/96) - embedded IPv4 is in the last 4 bytes.
    # NOTE: We intentionally only extract for the /96 prefix where the
    # last-4-bytes extraction is correct per RFC 6052 §2.2. The /48 discovery
    # prefix (64:ff9b:1::/48, RFC 8215) embeds IPv4 at a different offset
    # and is already blocked outright via _BLOCKED_IPV6_NETWORKS.
    if addr in _NAT64_PREFIX:
        raw = addr.packed
        return ipaddress.IPv4Address(raw[-4:])

    return None


def _ip_in_blocked_networks(
    addr: ipaddress.IPv4Address | ipaddress.IPv6Address,
    policy: SSRFPolicy,
) -> str | None:
    """Return a reason string if *addr* falls in a blocked range, else None."""
    if isinstance(addr, ipaddress.IPv4Address):
        # Always-blocked ranges (link-local, reserved, etc.)
        for net in _ALWAYS_BLOCKED_IPV4_NETWORKS:
            if addr in net:
                return "blocked IP range"
        # Loopback — only when block_localhost is set.
        if policy.block_localhost:
            for net in _LOOPBACK_IPV4_NETWORKS:
                if addr in net:
                    return "loopback IP range"
        # Private ranges (RFC 1918, CGN) — only when policy says so.
        if policy.block_private_ips:
            for net in _PRIVATE_IPV4_NETWORKS:
                if addr in net:
                    return "private IP range"
        for net in policy.additional_blocked_cidrs:
            if isinstance(net, ipaddress.IPv4Network) and addr in net:
                return "blocked CIDR"
    else:
        for net in _ALWAYS_BLOCKED_IPV6_NETWORKS:
            if addr in net:
                return "blocked IP range"
        # Loopback — only when block_localhost is set.
        if policy.block_localhost:
            for net in _LOOPBACK_IPV6_NETWORKS:
                if addr in net:
                    return "loopback IP range"
        if policy.block_private_ips:
            for net in _PRIVATE_IPV6_NETWORKS:
                if addr in net:
                    return "private IP range"
        for net in policy.additional_blocked_cidrs:
            if isinstance(net, ipaddress.IPv6Network) and addr in net:
                return "blocked CIDR"

    # Cloud metadata IP check
    if policy.block_cloud_metadata and str(addr) in _CLOUD_METADATA_IPS:
        return "cloud metadata endpoint"

    return None


# ---------------------------------------------------------------------------
# Public validation functions
# ---------------------------------------------------------------------------


def validate_resolved_ip(ip_str: str, policy: SSRFPolicy) -> None:
    """Validate a resolved IP address against the SSRF policy.

    Raises SSRFBlockedError if the IP is blocked.
    """
    try:
        addr = ipaddress.ip_address(ip_str)
    except ValueError as exc:
        raise SSRFBlockedError("invalid IP address") from exc

    if isinstance(addr, ipaddress.IPv6Address):
        # Check the original IPv6 address first — this catches addresses
        # in blocked IPv6 networks (e.g. NAT64 discovery prefix 64:ff9b:1::/48)
        # before we attempt IPv4 extraction.
        reason = _ip_in_blocked_networks(addr, policy)
        if reason is not None:
            raise SSRFBlockedError(reason)
        inner = _extract_embedded_ipv4(addr)
        if inner is not None:
            addr = inner

    reason = _ip_in_blocked_networks(addr, policy)
    if reason is not None:
        raise SSRFBlockedError(reason)


def validate_hostname(hostname: str, policy: SSRFPolicy) -> None:
    """Validate a hostname against the SSRF policy.

    Raises SSRFBlockedError if the hostname is blocked.
    """
    lower = hostname.lower()

    if policy.block_localhost and lower in _LOCALHOST_NAMES:
        raise SSRFBlockedError("localhost address")

    if policy.block_cloud_metadata and lower in _CLOUD_METADATA_HOSTNAMES:
        raise SSRFBlockedError("cloud metadata endpoint")

    if policy.block_k8s_internal and lower.endswith(_K8S_SUFFIX):
        raise SSRFBlockedError("Kubernetes internal DNS")


def _effective_allowed_hosts(policy: SSRFPolicy) -> frozenset[str]:
    """Return the policy's allowed_hosts set."""
    return policy.allowed_hosts


async def validate_url(url: str, policy: SSRFPolicy = SSRFPolicy()) -> None:
    """Validate a URL against the SSRF policy, including DNS resolution.

    This is the primary entry-point for async code paths. It delegates
    scheme/hostname/allowed-hosts checks to ``validate_url_sync``, then
    resolves DNS and validates every resolved IP.

    Raises SSRFBlockedError on any violation.
    """
    parsed = urllib.parse.urlparse(url)
    hostname = parsed.hostname or ""

    # Reuse synchronous checks (scheme, hostname, allowed-hosts bypass).
    try:
        validate_url_sync(url, policy)
    except SSRFBlockedError as exc:
        logger.warning(
            "ssrf_blocked",
            hostname=hostname,
            reason=str(exc),
            validation_type="write_time",
        )
        raise

    # If the host is in the allowed list, validate_url_sync returned
    # successfully and no DNS/IP checks are needed.
    allowed = {h.lower() for h in _effective_allowed_hosts(policy)}
    if hostname.lower() in allowed:
        return

    # If localhost is allowed and hostname is a localhost name, skip DNS/IP
    # checks — the resolved IP (e.g. link-local in Docker) is irrelevant
    # since we've explicitly permitted localhost access.
    if not policy.block_localhost and hostname.lower() in _LOCALHOST_NAMES:
        return

    # DNS resolution
    scheme = (parsed.scheme or "").lower()
    port = parsed.port or (443 if scheme == "https" else 80)
    try:
        addrinfo = await asyncio.to_thread(
            socket.getaddrinfo, hostname, port, type=socket.SOCK_STREAM
        )
    except socket.gaierror as exc:
        logger.warning(
            "ssrf_blocked",
            hostname=hostname,
            reason="DNS resolution failed",
            validation_type="write_time",
        )
        raise SSRFBlockedError("DNS resolution failed") from exc

    # Validate every resolved IP
    for _family, _type, _proto, _canonname, sockaddr in addrinfo:
        ip_str = sockaddr[0]
        try:
            validate_resolved_ip(ip_str, policy)
        except SSRFBlockedError as exc:
            logger.warning(
                "ssrf_blocked",
                hostname=hostname,
                reason=str(exc),
                validation_type="write_time",
            )
            raise


def validate_url_sync(url: str, policy: SSRFPolicy = SSRFPolicy()) -> None:
    """Synchronous URL validation (no DNS resolution).

    Suitable for Pydantic validators and other sync contexts. Checks scheme
    and hostname patterns only — use validate_url for full DNS-aware checking.

    Raises SSRFBlockedError on any violation.
    """
    parsed = urllib.parse.urlparse(url)

    # Scheme check
    scheme = (parsed.scheme or "").lower()
    if scheme not in policy.allowed_schemes:
        raise SSRFBlockedError(f"scheme '{scheme}' not allowed")

    # Hostname check
    hostname = parsed.hostname
    if not hostname:
        raise SSRFBlockedError("missing hostname")

    # Allowed-hosts bypass
    allowed = _effective_allowed_hosts(policy)
    if hostname.lower() in {h.lower() for h in allowed}:
        return

    # Hostname pattern checks
    validate_hostname(hostname, policy)
