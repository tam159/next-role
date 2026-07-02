import contextvars
import logging
import os
import threading
import typing

import structlog
from starlette.config import Config
from structlog.typing import EventDict

# env

log_env = Config()

LOG_JSON = log_env("LOG_JSON", cast=bool, default=False)
LOG_COLOR = log_env("LOG_COLOR", cast=bool, default=True)
LOG_LEVEL = log_env("LOG_LEVEL", cast=str, default="INFO")

logger = logging.getLogger()
logging.getLogger().setLevel(LOG_LEVEL.upper())
logging.getLogger("psycopg").setLevel(logging.WARNING)


class _GrpcCallbackFilter(logging.Filter):
    """Downgrade noisy gRPC PollerCompletionQueue callback errors to DEBUG."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno >= logging.WARNING:
            msg = record.getMessage()
            if "PollerCompletionQueue._handle_events" in msg:
                record.levelno = logging.DEBUG
                record.levelname = "DEBUG"
        return True


logging.getLogger("asyncio").addFilter(_GrpcCallbackFilter())
if hasattr(logger, "isEnabledFor"):
    LOG_LEVEL_DEBUG = logger.isEnabledFor(logging.DEBUG)
elif hasattr(logger, "is_enabled_for"):
    LOG_LEVEL_DEBUG = logger.is_enabled_for(logging.DEBUG)
else:
    LOG_LEVEL_DEBUG = False
del logger

worker_config = contextvars.ContextVar[dict[str, typing.Any] | None](
    "worker_config", default=None
)

# custom processors


def add_thread_name(
    logger: logging.Logger, method_name: str, event_dict: EventDict
) -> EventDict:
    event_dict["thread_name"] = threading.current_thread().name
    return event_dict


def set_logging_context(val: dict[str, typing.Any] | None) -> contextvars.Token:
    if val is None:
        return worker_config.set(None)
    current = worker_config.get()
    if current is None:
        return worker_config.set(val)
    return worker_config.set({**current, **val})


class AddPrefixedEnvVars:
    def __init__(self, prefix: str) -> None:
        self.kv = {
            key.removeprefix(prefix).lower(): value
            for key, value in os.environ.items()
            if key.startswith(prefix)
        }

    def __call__(
        self, logger: logging.Logger, method_name: str, event_dict: EventDict
    ) -> EventDict:
        event_dict.update(self.kv)
        return event_dict


class AddStaticMetadata:
    def __init__(self) -> None:
        self._has_deepagents = os.getenv("DEEPAGENTS_VERSION", "") != ""

    def __call__(
        self, logger: logging.Logger, method_name: str, event_dict: EventDict
    ) -> EventDict:
        try:
            from langgraph_api import __version__  # noqa: PLC0415

            event_dict["langgraph_api_version"] = __version__
        except ImportError:
            pass
        if self._has_deepagents:
            event_dict["has_deepagents"] = "true"
        return event_dict


class AddLoggingContext:
    def __init__(self):
        try:
            from langchain_core.runnables.config import (  # noqa: PLC0415
                RunnableConfig,
                var_child_runnable_config,
            )

            self.cvar: contextvars.ContextVar[RunnableConfig | None] = (
                var_child_runnable_config
            )
        except Exception:
            self.cvar = False

    def __call__(
        self, logger: logging.Logger, method_name: str, event_dict: EventDict
    ) -> EventDict:
        if (ctx := worker_config.get()) is not None:
            event_dict.update(ctx)
        lgnode = None
        if (
            self.cvar is not None
            and (conf := self.cvar.get())
            and (metadata := conf.get("metadata"))
            and (lgnode := metadata.get("langgraph_node"))
        ):
            event_dict["langgraph_node"] = lgnode
        return event_dict


class JSONRenderer:
    def __call__(
        self, logger: logging.Logger, method_name: str, event_dict: EventDict
    ) -> str:
        """
        The return type of this depends on the return type of self._dumps.
        """
        from langgraph_api.serde import json_dumpb  # noqa: PLC0415

        return json_dumpb(event_dict).decode()


# same as Formatter, but always uses JSONRenderer. Used by OTLP log handler for self hosted logging
class OTLPFormatter(structlog.stdlib.ProcessorFormatter):
    def __init__(self, *args, **kwargs) -> None:
        if len(args) == 3:
            fmt, datefmt, style = args
            kwargs["fmt"] = fmt
            kwargs["datefmt"] = datefmt
            kwargs["style"] = style
        else:
            raise RuntimeError(
                f"OTLPFormatter expected 3 positional arguments (fmt, datefmt, style), "
                f"but got {len(args)} arguments."
            )
        super().__init__(
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                AddLoggingContext(),
                JSONRenderer(),
            ],
            foreign_pre_chain=shared_processors,
            **kwargs,
        )


LEVELS = logging.getLevelNamesMapping()


# shared config, for both logging and structlog

shared_processors = [
    add_thread_name,
    structlog.stdlib.add_logger_name,
    structlog.stdlib.add_log_level,
    structlog.stdlib.PositionalArgumentsFormatter(),
    structlog.stdlib.ExtraAdder(),
    AddPrefixedEnvVars("LANGSMITH_LANGGRAPH_"),  # injected by docker build
    AddStaticMetadata(),
    structlog.processors.TimeStamper(fmt="iso", utc=True),
    structlog.processors.StackInfoRenderer(),
    structlog.processors.format_exc_info,
    structlog.processors.UnicodeDecoder(),
    AddLoggingContext(),
]


# configure logging, used by logging.json, applied by uvicorn

renderer = (
    JSONRenderer() if LOG_JSON else structlog.dev.ConsoleRenderer(colors=LOG_COLOR)
)


class Formatter(structlog.stdlib.ProcessorFormatter):
    def __init__(self, *args, **kwargs) -> None:
        if len(args) == 3:
            fmt, datefmt, style = args
            kwargs["fmt"] = fmt
            kwargs["datefmt"] = datefmt
            kwargs["style"] = style
        else:
            raise RuntimeError("Invalid number of arguments")
        super().__init__(
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                AddLoggingContext(),
                renderer,
            ],
            foreign_pre_chain=shared_processors,
            **kwargs,
        )


# configure structlog
if not structlog.is_configured():
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
