from typing import Any

FILTERED_ROUTES = {"/ok", "/info", "/metrics", "/docs", "/openapi.json"}

HTTP_LATENCY_BUCKETS = [
    0.01,
    0.1,
    0.5,
    1,
    5,
    15,
    30,
    60,
    120,
    300,
    600,
    1800,
    3600,
    float("inf"),
]


def get_route(route: Any) -> str | None:
    try:
        # default lg api routes use the custom APIRoute where scope["route"] is set to a string
        if isinstance(route, str):
            return route
        else:
            # custom FastAPI routes provided by user_router attach an object to scope["route"]
            route_path = getattr(route, "path", None)
            return route_path
    except Exception:
        return None


def should_filter_route(route_path: str) -> bool:
    # use endswith to honor MOUNT_PREFIX
    return any(route_path.endswith(suffix) for suffix in FILTERED_ROUTES)
