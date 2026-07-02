import os
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from langchain_core.runnables import Runnable

from langgraph_api.schema import Config

_DC_KWARGS = {"kw_only": True, "slots": True, "frozen": True}

JS_EXTENSIONS = (
    ".ts",
    ".mts",
    ".cts",
    ".js",
    ".mjs",
    ".cjs",
)


def is_js_path(path: str | None) -> bool:
    if path is None:
        return False
    return os.path.splitext(path)[1] in JS_EXTENSIONS


@dataclass(**_DC_KWARGS)
class RemoteInterrupt:
    raw: dict

    @property
    def id(self) -> str:
        return self.raw["id"]

    @property
    def value(self) -> Any:
        return self.raw["value"]

    @property
    def ns(self) -> Sequence[str] | None:
        return self.raw.get("ns")

    @property
    def resumable(self) -> bool:
        return self.raw.get("resumable", True)

    @property
    def when(self) -> Literal["during"]:
        return self.raw.get("when", "during")


class BaseRemotePregel(Runnable):
    name: str = "LangGraph"

    graph_id: str

    # Config passed from get_graph()
    config: Config
