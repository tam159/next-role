import dataclasses
import functools
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import structlog
from starlette.exceptions import HTTPException

from langgraph_api.config import HTTP_CONFIG, WEBHOOKS_CONFIG
from langgraph_api.config.schemas import WebhookUrlPolicy
from langgraph_api.http import (
    ensure_webhook_http_client,
    get_loopback_client,
    http_request,
)
from langgraph_api.lc_security import SSRFBlockedError, SSRFPolicy, validate_url
from langgraph_api.serde import json_dumpb, json_loads

if TYPE_CHECKING:
    from langgraph_api.worker import WorkerResult

logger = structlog.stdlib.get_logger(__name__)


def _serialize_exception(exc: BaseException) -> dict:
    """Serialize an exception into a structured error dict.

    Falls back to a minimal dict if serialization itself fails.
    """
    try:
        return json_loads(json_dumpb(exc))
    except Exception:
        return {"error": type(exc).__name__, "message": str(exc)}


def _filter_webhook_payload(payload: dict, allowed_fields: set[str] | None) -> dict:
    if not allowed_fields:
        return payload
    return {key: value for key, value in payload.items() if key in allowed_fields}


@dataclasses.dataclass(frozen=True)
class _WebhookConfig:
    """Parsed, immutable snapshot of WEBHOOKS_CONFIG — built once."""

    allowed_domains: tuple[str, ...]
    allowed_ports: tuple[int, ...] | None
    max_url_length: int
    require_https: bool
    disable_loopback: bool
    disable_private_ips: bool
    # Non-wildcard domains, lowered — used as SSRF allowed hosts.
    exact_allowed_hosts: frozenset[str]
    # Wildcard bases (e.g. "mycorp.com" from "*.mycorp.com"), lowered.
    wildcard_bases: tuple[str, ...]
    # Base SSRFPolicy with only the exact (non-wildcard) allowed hosts.
    base_ssrf_policy: SSRFPolicy


@functools.cache
def _get_webhook_config() -> _WebhookConfig:
    """Build webhook config from WEBHOOKS_CONFIG (cached after first call)."""
    cfg = WEBHOOKS_CONFIG
    policy_cfg = WebhookUrlPolicy(cfg.get("url") or {}) if cfg else {}
    allowed_domains = tuple(policy_cfg.get("allowed_domains") or [])
    raw_ports = policy_cfg.get("allowed_ports")
    allowed_ports = tuple(raw_ports) if raw_ports else None

    exact_hosts: set[str] = set()
    wildcard_bases: list[str] = []
    for pattern in allowed_domains:
        p = pattern.strip().lower()
        if p.startswith("*."):
            wildcard_bases.append(p[2:])
        else:
            exact_hosts.add(p)

    exact_allowed = frozenset(exact_hosts)

    disable_private_ips = bool(policy_cfg.get("disable_private_ips", False))
    # Loopback webhooks are denied by default (covers relative URLs that
    # would dispatch through the in-process ASGI client at root_path=/noauth,
    # plus localhost / 127.x / ::1 / host.docker.internal absolute URLs,
    # plus any hostname that DNS-resolves into the loopback range). This is
    # the fix for GHSA-q3v5-r5ch-p57j: relative-URL webhooks were the auth
    # bypass primitive, and the localhost/loopback IP variants are the
    # broader SSRF surface that the same flag governs via SSRFPolicy.
    disable_loopback = bool(policy_cfg.get("disable_loopback", True))

    return _WebhookConfig(
        allowed_domains=allowed_domains,
        allowed_ports=allowed_ports,
        max_url_length=int(policy_cfg.get("max_url_length", 4096)),
        # TODO: We should flip this in the next minor release
        require_https=bool(policy_cfg.get("require_https", False)),
        disable_loopback=disable_loopback,
        disable_private_ips=disable_private_ips,
        exact_allowed_hosts=exact_allowed,
        wildcard_bases=tuple(wildcard_bases),
        base_ssrf_policy=SSRFPolicy(
            block_private_ips=disable_private_ips,
            block_localhost=disable_loopback,
            allowed_hosts=exact_allowed,
        ),
    )


async def validate_webhook_url_or_raise(url: str) -> None:
    """Validate a user-provided webhook URL against configured policy.

    Always applies SSRF protection (private IPs, metadata endpoints, etc.).
    When WEBHOOKS_CONFIG is set, also enforces domain allowlists, port
    restrictions, HTTPS requirements, and loopback policy.
    """
    wh = _get_webhook_config()

    if len(url) > wh.max_url_length:
        raise HTTPException(status_code=422, detail="Webhook URL too long")

    # Relative loopback URL (internal route) — dispatched via an in-process
    # ASGI client that mounts under root_path="/noauth", which the auth
    # middleware treats as an auth-bypass marker. Denied by default so a
    # user-supplied webhook URL cannot be turned into an unauthenticated
    # call against the server's own routes (GHSA-q3v5-r5ch-p57j). Operators
    # who intentionally route webhooks to in-process routes can opt back in
    # by setting webhooks.url.disable_loopback to false.
    if url.startswith("/"):
        if wh.disable_loopback:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Loopback webhooks (relative URLs and localhost) are "
                    "disabled by default. They bypass authentication via the "
                    "in-process ASGI transport and can be abused to invoke "
                    "internal routes as an unauthenticated caller. Set "
                    "webhooks.url.disable_loopback to false (in langgraph.json "
                    "or via LANGGRAPH_WEBHOOKS) to opt back in — only do so "
                    "when you control the mounted routes."
                ),
            )
        return

    parsed = urlparse(url)
    if wh.require_https and parsed.scheme.lower() != "https":
        raise HTTPException(status_code=422, detail="Webhook must use https")

    # Port policy
    if wh.allowed_ports:
        if parsed.port is not None:
            port = parsed.port
        else:
            port = 443 if parsed.scheme == "https" else 80
        if port not in wh.allowed_ports:
            raise HTTPException(
                status_code=422, detail=f"Webhook port {port} not allowed"
            )

    host = parsed.hostname or ""
    if not host:
        raise HTTPException(
            status_code=422, detail=f"Invalid webhook hostname '{host}'"
        )

    # Domain allowlist check (kept separate from SSRF — this is a business rule)
    host_lower = host.lower()
    if wh.allowed_domains:
        host_allowed = host_lower in wh.exact_allowed_hosts or any(
            host_lower.endswith("." + wb) for wb in wh.wildcard_bases
        )
        if not host_allowed:
            raise HTTPException(status_code=422, detail="Webhook domain not allowed")

    # Build SSRF policy.  The base policy (with exact allowed hosts) is
    # cached; only extend it when the host matched a wildcard pattern.
    if host_lower not in wh.exact_allowed_hosts and any(
        host_lower.endswith("." + wb) for wb in wh.wildcard_bases
    ):
        ssrf_policy = dataclasses.replace(
            wh.base_ssrf_policy,
            allowed_hosts=wh.exact_allowed_hosts | frozenset({host_lower}),
        )
    else:
        ssrf_policy = wh.base_ssrf_policy

    try:
        await validate_url(url, ssrf_policy)
    except SSRFBlockedError as exc:
        raise HTTPException(
            status_code=422, detail=f"Webhook host blocked: {exc.reason}"
        ) from exc


async def call_webhook(result: "WorkerResult") -> None:
    if HTTP_CONFIG and HTTP_CONFIG.get("disable_webhooks"):
        logger.info(
            "Webhooks disabled, skipping webhook call", webhook=result["webhook"]
        )
        return

    checkpoint = result["checkpoint"]
    payload = {
        **result["run"],
        "status": result["status"],
        "run_started_at": result["run_started_at"],
        "run_ended_at": result["run_ended_at"],
        "webhook_sent_at": datetime.now(UTC).isoformat(),
        "values": checkpoint["values"] if checkpoint else None,
    }
    if exception := result["exception"]:
        payload["error"] = _serialize_exception(exception)

    allowed_fields = WEBHOOKS_CONFIG.get("allowed_fields") if WEBHOOKS_CONFIG else None
    payload = _filter_webhook_payload(payload, allowed_fields)

    webhook = result.get("webhook")
    if webhook:
        try:
            # We've already validated on ingestion, but you could technically have an issue if you re-deployed with a different environment
            await validate_webhook_url_or_raise(webhook)
            # Note: header templates should have already been evaluated against the env at load time.
            headers = WEBHOOKS_CONFIG.get("headers") if WEBHOOKS_CONFIG else None

            if webhook.startswith("/"):
                # Call into this own app
                webhook_client = get_loopback_client()
            else:
                webhook_client = await ensure_webhook_http_client()
            await http_request(
                "POST",
                webhook,
                json=payload,
                headers=headers,
                client=webhook_client,
            )
            await logger.ainfo(
                "Background worker called webhook",
                webhook=result["webhook"],
                run_id=str(result["run"]["run_id"]),
            )
        except Exception as exc:
            logger.exception(
                f"Background worker failed to call webhook {result['webhook']}",
                exc_info=exc,
                webhook=result["webhook"],
            )
