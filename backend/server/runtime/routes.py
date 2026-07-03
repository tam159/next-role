"""Route discovery facade for runtime packages.

This module discovers and exposes internal routes from the active runtime.
Routes are loaded based on MIGRATIONS_PATH which determines the runtime type.
"""

from collections.abc import Callable

from starlette.routing import Route

get_internal_routes: Callable[[], list[Route]] | None = None
