from __future__ import annotations

from ipaddress import IPv6Address, ip_address


def normalize_host(host: str) -> str:
    """Normalize host literals from env/config.

    Brackets are valid in URLs and host:port strings, but socket bind/connect APIs
    expect the raw IPv6 literal.
    """
    if host.startswith("[") and host.endswith("]"):
        return host[1:-1]
    return host


def format_hostport(host: str, port: int) -> str:
    host = normalize_host(host)
    if ":" in host:
        return f"[{host}]:{port}"
    return f"{host}:{port}"


def get_healthcheck_target_host(bind_host: str) -> str:
    bind_host = normalize_host(bind_host)
    if bind_host in {"", "0.0.0.0"}:
        return "127.0.0.1"
    if bind_host == "::":
        return "::1"
    return bind_host


def get_healthcheck_url_host(host: str) -> str:
    host = normalize_host(host)
    if host in {"0.0.0.0", ""}:
        return "localhost"
    if host == "::":
        return "[::1]"

    try:
        host_ip = ip_address(host)
    except ValueError:
        return host

    return (
        f"[{host_ip.compressed}]"
        if isinstance(host_ip, IPv6Address)
        else host_ip.compressed
    )
