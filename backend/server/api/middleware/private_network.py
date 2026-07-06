"""Middleware to handle private network requests for CORS preflight."""

from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp


class PrivateNetworkMiddleware(BaseHTTPMiddleware):
    """Add Access-Control-Allow-Private-Network header for preflight requests.

    This middleware intercepts OPTIONS requests and adds the
    Access-Control-Allow-Private-Network header to the response, allowing private
    network access for CORS preflight requests.

    For context see:
    * https://github.com/langchain-ai/langgraph/issues/3261
    * https://developer.chrome.com/blog/private-network-access-update-2024-03

    We should only automatically turn it on for the loopback address, when users
    use `langgraph dev`.

    A web browser determines whether a network is private based on IP address ranges
    and local networking conditions. Typically, it checks:

    IP Address Range - If the website is hosted on an IP within private address
    ranges (RFC 1918):

    10.0.0.0 - 10.255.255.255
    172.16.0.0 - 172.31.255.255
    192.168.0.0 - 192.168.255.255
    127.0.0.1 (loopback)
    Localhost and Hostname - Domains like localhost or .local are assumed to be private.

    Network Context - The browser may check if the device is connected
    to a local network (e.g., corporate or home Wi-Fi) rather than the public internet.

    CORS and Private Network Access (PNA) - Modern browsers implement restrictions
    where resources on private networks require explicit permission (via CORS headers)
    when accessed from a public site.
    """

    def __init__(self, app: ASGIApp):
        """Initialize middleware."""
        super().__init__(app)

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        response = await call_next(request)
        if request.method == "OPTIONS":  # Handle preflight requests
            response.headers["Access-Control-Allow-Private-Network"] = "true"
        return response
