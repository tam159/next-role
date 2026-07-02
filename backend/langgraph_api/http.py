import asyncio
import contextlib

import httpx
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from langgraph_api.serde import json_dumpb


class JsonHttpClient:
    """HTTPX client for JSON requests."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        """Initialize the auth client."""
        self.client = client

    async def post(
        self,
        path: str,
        /,
        *,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        json: dict | None = None,
        content: bytes | None = None,
        connect_timeout: float | None = None,
        request_timeout: float | None = None,
        total_timeout: float | None = None,
        raise_error: bool = True,
    ) -> None:
        request_headers = {"Content-Type": "application/json"}
        if headers:
            request_headers.update(headers)

        if json and content:
            raise ValueError("Cannot specify both 'json' and 'content'")

        try:
            res = await asyncio.wait_for(
                self.client.post(
                    path,
                    content=json_dumpb(json) if json else content,
                    headers=request_headers,
                    timeout=(
                        httpx.Timeout(
                            request_timeout or connect_timeout,
                            connect=connect_timeout,
                            read=request_timeout,
                        )
                        if connect_timeout or request_timeout
                        else None
                    ),
                    params=params,
                ),
                # httpx timeout controls are additive for each operation
                # (connect, read, write), so we need an asyncio timeout instead
                total_timeout,
            )
            # Raise for retriable errors
            if raise_error:
                res.raise_for_status()
        finally:
            # We don't need the response body, so we close the response
            with contextlib.suppress(UnboundLocalError):
                await res.aclose()


_http_client: JsonHttpClient | None = None
_loopback_client: JsonHttpClient | None = None


async def start_http_client() -> JsonHttpClient:
    global _http_client
    _http_client = JsonHttpClient(
        client=httpx.AsyncClient(
            transport=httpx.AsyncHTTPTransport(
                retries=2,  # this applies only to ConnectError, ConnectTimeout
                limits=httpx.Limits(
                    max_keepalive_connections=10, keepalive_expiry=60.0
                ),
            ),
        ),
    )
    return _http_client


async def stop_http_client() -> None:
    global _http_client
    if _http_client is None:
        return
    await _http_client.client.aclose()
    _http_client = None


def get_http_client() -> JsonHttpClient | None:
    global _http_client
    return _http_client


async def ensure_http_client() -> JsonHttpClient:
    global _http_client
    if _http_client is None:
        return await start_http_client()
    return _http_client


def get_loopback_client() -> JsonHttpClient:
    global _loopback_client
    if _loopback_client is None:
        from langgraph_api.server import app  # noqa: PLC0415

        _loopback_client = JsonHttpClient(
            client=httpx.AsyncClient(
                base_url="http://api",
                transport=httpx.ASGITransport(app, root_path="/noauth"),
            ),
        )
    return _loopback_client


_webhook_http_client: JsonHttpClient | None = None


async def ensure_webhook_http_client() -> JsonHttpClient:
    """Return (or create) an SSRF-safe HTTP client for webhook dispatch."""
    global _webhook_http_client
    if _webhook_http_client is not None:
        return _webhook_http_client

    from langgraph_api.lc_security import ssrf_safe_async_client  # noqa: PLC0415
    from langgraph_api.webhook import _get_webhook_config  # noqa: PLC0415

    inner = ssrf_safe_async_client(
        policy=_get_webhook_config().base_ssrf_policy,
        retries=2,
        limits=httpx.Limits(max_keepalive_connections=10, keepalive_expiry=60.0),
        follow_redirects=True,
        max_redirects=5,
    )
    _webhook_http_client = JsonHttpClient(client=inner)
    return _webhook_http_client


async def stop_webhook_http_client() -> None:
    global _webhook_http_client
    if _webhook_http_client is None:
        return
    await _webhook_http_client.client.aclose()
    _webhook_http_client = None


def is_retriable_error(exception: BaseException) -> bool:
    # httpx error hierarchy: https://www.python-httpx.org/exceptions/
    # Retry all timeout related errors
    if isinstance(exception, httpx.TimeoutException | httpx.NetworkError):
        return True
    # Seems to just apply to HttpStatusError but doesn't hurt to check all
    if isinstance(exception, httpx.HTTPError):
        response = getattr(exception, "response", None)
        return response is not None and (
            response.status_code >= 500 or response.status_code == 429
        )
    return False


retry_http = retry(
    reraise=True,
    retry=retry_if_exception(is_retriable_error),
    wait=wait_exponential_jitter(),
    stop=stop_after_attempt(3),
)


@retry_http
async def http_request(
    method: str,
    path: str,
    /,
    *,
    params: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
    body: bytes | str | None = None,
    json: dict | None = None,
    connect_timeout: float | None = 5,
    request_timeout: float | None = 30,
    raise_error: bool = True,
    client: JsonHttpClient | None = None,
) -> None:
    """Make an HTTP request with retries.

    Args:
        method: HTTP method
        path: URL path
        params: Query parameters
        headers: Request headers
        body: Raw request body (bytes or str)
        json: JSON body (mutually exclusive with body)
        connect_timeout: Connection timeout in seconds
        request_timeout: Request timeout in seconds
        raise_error: Whether to raise for HTTP errors

    Returns:
        httpx.Response object
    """
    if not path.startswith(("http://", "https://", "/")):
        raise ValueError("path must start with / or http")

    client = client or (await ensure_http_client())

    content = None
    if body is not None:
        content = body.encode("utf-8") if isinstance(body, str) else body
    elif json is not None:
        content = json_dumpb(json)

    if method.upper() == "POST":
        return await client.post(
            path,
            params=params,
            headers=headers,
            content=content,
            connect_timeout=connect_timeout,
            request_timeout=request_timeout,
            raise_error=raise_error,
        )
    else:
        raise ValueError(f"Unsupported HTTP method: {method}")
