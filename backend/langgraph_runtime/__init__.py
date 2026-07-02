import importlib.util
import os
import sys

import structlog

logger = structlog.stdlib.get_logger(__name__)

try:
    RUNTIME_EDITION = os.environ["LANGGRAPH_RUNTIME_EDITION"]
    RUNTIME_PACKAGE = f"langgraph_runtime_{RUNTIME_EDITION}"
except KeyError:
    raise ValueError(
        "LANGGRAPH_RUNTIME_EDITION environment variable is not set."
        " Expected LANGGRAPH_RUNTIME_EDITION to be set to one of:\n"
        " - inmem\n"
        " - postgres\n"
        " - community\n"
    ) from None
if importlib.util.find_spec(RUNTIME_PACKAGE):
    backend = importlib.import_module(RUNTIME_PACKAGE)
    logger.info(f"Using {RUNTIME_PACKAGE}")
else:
    raise ImportError(
        "Langgraph runtime backend not found. Please install with "
        f'`pip install "langgraph-runtime-{RUNTIME_EDITION}"`'
    ) from None

# All runtime backends share the same API
for module_name in (
    "checkpoint",
    "database",
    "lifespan",
    "ops",
    "retry",
    "store",
    "metrics",
    "routes",
):
    mod = getattr(backend, module_name, None)
    if mod is not None:
        sys.modules["langgraph_runtime." + module_name] = mod
