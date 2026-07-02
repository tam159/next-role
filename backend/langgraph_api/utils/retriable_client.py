import asyncio

import httpx
import structlog

logger = structlog.stdlib.get_logger(__name__)


async def _make_http_request_with_retries(
    url: str,
    headers: dict,
    method: str = "GET",
    json_data: dict | None = None,
    max_retries: int = 3,
    base_delay: float = 1.0,
) -> httpx.Response:
    """
    Make an HTTP request with exponential backoff retries.

    Args:
        url: The URL to request
        headers: Headers to include in the request
        method: HTTP method ("GET" or "POST")
        json_data: JSON data for POST requests
        max_retries: Maximum number of retry attempts
        base_delay: Base delay in seconds for exponential backoff

    Returns:
        httpx.Response: The successful response

    Raises:
        httpx.HTTPStatusError: If the request fails after all retries
        httpx.RequestError: If the request fails after all retries
    """
    for attempt in range(max_retries + 1):
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.request(
                    method, url, headers=headers, json=json_data
                )
                response.raise_for_status()
                return response

        except (
            httpx.TimeoutException,
            httpx.NetworkError,
            httpx.RequestError,
            httpx.HTTPStatusError,
        ) as e:
            if isinstance(e, httpx.HTTPStatusError) and e.response.status_code < 500:
                # Don't retry on 4xx errors, but do on 5xxs
                raise e

            # Back off and retry if we haven't reached the max retries
            if attempt < max_retries:
                delay = base_delay * (2**attempt)  # Exponential backoff
                logger.warning(
                    "HTTP %s request attempt %d to %s failed: %s. Retrying in %.1f seconds...",
                    method,
                    attempt + 1,
                    url,
                    e,
                    delay,
                )
                await asyncio.sleep(delay)
            else:
                logger.exception(
                    "HTTP %s request to %s failed after %d attempts. Last error: %s",
                    method,
                    url,
                    max_retries + 1,
                    e,
                )
                raise e

    # Unreachable when max_retries >= 0, but keeps the type checker happy.
    msg = f"HTTP {method} request to {url} failed: no attempts made"
    raise httpx.RequestError(msg)
