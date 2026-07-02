from __future__ import annotations

import traceback
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from langgraph_api.graph import GraphSpec


class GraphLoadError(RuntimeError):
    """Raised when a user provided graph fails to load."""

    def __init__(self, spec: GraphSpec, cause: Exception):
        self.spec = spec
        self.cause = cause
        self.location = spec.module or spec.path or "<unknown>"
        self.notes = tuple(getattr(cause, "__notes__", ()) or ())
        self._traceback = traceback.TracebackException.from_exception(
            cause, capture_locals=False
        )
        self._exception_only = list(self._traceback.format_exception_only())
        message = f"Failed to load graph '{spec.id}' from {self.location}: {cause}"
        super().__init__(message)

    @property
    def hint(self) -> str | None:
        if isinstance(self.cause, ImportError):
            return "Check that your project dependencies are installed and imports are correct."
        return None

    def log_fields(self) -> dict[str, Any]:
        fields: dict[str, Any] = {
            "graph_id": self.spec.id,
            "location": self.location,
            "error_type": type(self.cause).__name__,
            "error_message": self.cause_message,
            "error_boundary": "user_graph",
            "summary": self.summary,
        }
        if self.hint:
            fields["hint"] = self.hint
        if self.notes:
            fields["notes"] = "\n".join(self.notes)
        fields["user_traceback"] = self.user_traceback()
        return fields

    @property
    def cause_message(self) -> str:
        if self._exception_only:
            return self._exception_only[0].strip()
        return str(self.cause)

    @property
    def summary(self) -> str:
        return f"{type(self.cause).__name__}: {self.cause_message}"

    def user_traceback(self) -> str:
        """Return the full traceback without filtering."""
        return "".join(self._traceback.format())


class HealthServerStartupError(RuntimeError):
    def __init__(self, host: str, port: int, cause: BaseException):
        self.host = host
        self.port = port
        self.cause = cause
        port_desc = (
            f"{host}:{port}" if host not in {"0.0.0.0", "::"} else f"port {port}"
        )
        if isinstance(cause, OSError) and cause.errno in {48, 98}:
            message = (
                f"Health/metrics server could not bind to {port_desc}: "
                "address already in use. Stop the other process or set PORT to a free port."
            )
        else:
            message = f"Health/metrics server failed to start on {port_desc}: {cause}"
        super().__init__(message)
