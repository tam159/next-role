from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress
from typing import Any

import httpx
from httpx._types import HeaderTypes, QueryParamTypes, RequestData
from tenacity import retry
from tenacity.retry import retry_if_exception
from tenacity.stop import stop_after_attempt
from tenacity.wait import wait_exponential_jitter

from langgraph_api.config import LANGSMITH_AUTH_ENDPOINT

_client: "JsonHttpClient"


def is_retriable_error(exception: BaseException) -> bool:
    if isinstance(exception, httpx.TransportError):
        return True
    return (
        isinstance(exception, httpx.HTTPStatusError)
        and exception.response.status_code > 499
    )


retry_httpx = retry(
    reraise=True,
    retry=retry_if_exception(is_retriable_error),
    wait=wait_exponential_jitter(),
    stop=stop_after_attempt(3),
)


class JsonHttpClient:
    """HTTPX client for JSON requests, with retries."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        """Initialize the auth client."""
        self.client = client

    async def _get(
        self,
        path: str,
        *,
        params: QueryParamTypes | None = None,
        headers: HeaderTypes | None = None,
    ) -> httpx.Response:
        return await self.client.get(path, params=params, headers=headers)

    @retry_httpx
    async def get(
        self,
        path: str,
        *,
        params: QueryParamTypes | None = None,
        headers: HeaderTypes | None = None,
    ) -> httpx.Response:
        return await self.client.get(path, params=params, headers=headers)

    async def _post(
        self,
        path: str,
        *,
        data: RequestData | None = None,
        json: Any | None = None,
        params: QueryParamTypes | None = None,
        headers: HeaderTypes | None = None,
    ) -> httpx.Response:
        return await self.client.post(
            path, data=data, json=json, params=params, headers=headers
        )

    @retry_httpx
    async def post(
        self,
        path: str,
        *,
        data: RequestData | None = None,
        json: Any | None = None,
        params: QueryParamTypes | None = None,
        headers: HeaderTypes | None = None,
    ) -> httpx.Response:
        return await self.client.post(
            path, data=data, json=json, params=params, headers=headers
        )


def create_client(base_url: str | None = None) -> JsonHttpClient:
    """Create the auth http client."""
    url = base_url if base_url is not None else LANGSMITH_AUTH_ENDPOINT

    return JsonHttpClient(
        httpx.AsyncClient(
            transport=httpx.AsyncHTTPTransport(
                retries=5,  # this applies only to ConnectError, ConnectTimeout
                limits=httpx.Limits(
                    max_keepalive_connections=40,
                    keepalive_expiry=240.0,
                ),
            ),
            timeout=httpx.Timeout(2.0),
            base_url=url,
        )
    )


async def close_auth_client() -> None:
    """Close the auth http client."""
    global _client
    with suppress(NameError):
        await _client.client.aclose()


async def initialize_auth_client(base_url: str | None = None) -> None:
    """Initialize the auth http client."""
    await close_auth_client()
    global _client
    _client = create_client(base_url=base_url)


@asynccontextmanager
async def auth_client(
    base_url: str | None = None,
) -> AsyncGenerator[JsonHttpClient, None]:
    """Get the auth http client."""
    url = base_url if base_url is not None else LANGSMITH_AUTH_ENDPOINT
    # pytest does something funny with event loops,
    # so we can't use a global pool for tests
    if url.startswith("http://localhost"):
        client = create_client(base_url=url)
        try:
            yield client
        finally:
            await client.client.aclose()
    else:
        try:
            found = bool(not _client.client.is_closed)
        except NameError:
            found = False
        if found:
            yield _client
        else:
            await initialize_auth_client(base_url=url)
            yield _client
