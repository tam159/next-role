import json
import os
import re

from anyio import open_file
from orjson import loads
from starlette.responses import Response
from starlette.routing import BaseRoute, Mount
from starlette.staticfiles import StaticFiles
from typing_extensions import TypedDict

from langgraph_api.js.ui import UI_PUBLIC_DIR, UI_SCHEMAS_FILE
from langgraph_api.route import ApiRequest, ApiRoute


class UiSchema(TypedDict):
    name: str
    assets: list[str]


_UI_SCHEMAS_CACHE: dict[str, UiSchema] | None = None


async def load_ui_schemas() -> dict[str, UiSchema]:
    """Load and cache UI schema mappings from JSON file."""
    global _UI_SCHEMAS_CACHE

    if _UI_SCHEMAS_CACHE is not None:
        return _UI_SCHEMAS_CACHE

    if not UI_SCHEMAS_FILE.exists():
        _UI_SCHEMAS_CACHE = {}
    else:
        async with await open_file(UI_SCHEMAS_FILE, mode="r") as f:
            _UI_SCHEMAS_CACHE = loads(await f.read())

    return _UI_SCHEMAS_CACHE


async def handle_ui(request: ApiRequest) -> Response:
    """Serve UI HTML with appropriate script/style tags."""
    graph_id = request.path_params["graph_id"]
    host = request.headers.get("host")
    message = await request.json(schema=None)

    # Load UI file paths from schema
    schemas = await load_ui_schemas()

    if graph_id not in schemas:
        return Response(f"UI not found for graph '{graph_id}'", status_code=404)

    result = []
    for filepath in schemas[graph_id]["assets"]:
        basename = os.path.basename(filepath)
        ext = os.path.splitext(basename)[1]

        # Use http:// protocol if accessing a localhost service
        def is_host(needle: str) -> bool:
            if not isinstance(host, str):
                return False
            return host.startswith(needle + ":") or host == needle

        protocol = "http:" if is_host("localhost") or is_host("127.0.0.1") else ""
        valid_js_name = re.sub(r"[^a-zA-Z0-9]", "_", graph_id)

        if ext == ".css":
            result.append(
                f'<link rel="stylesheet" href="{protocol}//{host}/ui/{graph_id}/{basename}" />'
            )
        elif ext == ".js":
            safe_name = json.dumps(message["name"]).replace("'", "&#39;")
            result.append(
                f'<script src="{protocol}//{host}/ui/{graph_id}/{basename}" '
                f"onload='__LGUI_{valid_js_name}.render({safe_name}, \"{{{{shadowRootId}}}}\")'>"
                "</script>"
            )

    return Response(content="\n".join(result), headers={"Content-Type": "text/html"})


ui_routes: list[BaseRoute] = [
    ApiRoute("/ui/{graph_id}", handle_ui, methods=["POST"]),
    Mount("/ui", StaticFiles(directory=UI_PUBLIC_DIR, check_dir=False)),
]
