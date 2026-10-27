"""Tool handlers RunLedger calls while recording this suite.

Replay mode never imports this module. It returns the committed cassette.
Record mode calls the career-agent implementations:

- ``web_search`` is the ``@tool`` in ``backend/agents/career_agent/tools.py``.
- ``write_file`` follows the deepagents ``write_file`` path check, then
  ``CompositeBackend.write`` into the ``/tailored_resume/`` ``ObjectStoreBackend``
  route. The store is ``obstore``'s ``MemoryStore`` (the same stand-in the
  unit tests use) so recording does not need SeaweedFS. The YAML is normalized
  with ``_normalize_resume_yaml`` and must validate as rendercv's
  ``RenderCVModel`` before the write result is returned.
"""

from __future__ import annotations

import json
import tempfile
from typing import Any

_TAILORED_PREFIX = "/tailored_resume/"


def web_search(args: dict[str, Any]) -> dict[str, Any]:
    """Run the career agent's ``web_search`` tool and return its payload."""
    from backend.agents.career_agent.tools import web_search as search_tool

    result = search_tool.invoke(args)
    if not isinstance(result, dict):
        message = f"web_search returned {type(result).__name__}, expected dict"
        raise TypeError(message)
    return result


def _resume_document(content: str) -> dict[str, Any]:
    """Normalize and validate tailored-resume YAML. Return the parsed document."""
    import yaml
    from rendercv.schema.models.rendercv_model import RenderCVModel

    from backend.agents.career_agent.tools import _normalize_resume_yaml

    body, _fixes = _normalize_resume_yaml(content)
    parsed = yaml.safe_load(body)
    if not isinstance(parsed, dict):
        message = "resume YAML must be a mapping"
        raise TypeError(message)
    RenderCVModel.model_validate(parsed)
    document = json.loads(json.dumps(parsed))
    if not isinstance(document, dict):
        message = "validated resume did not serialize to an object"
        raise TypeError(message)
    return document


def write_file(args: dict[str, Any]) -> dict[str, Any]:
    """Validate resume YAML and write it on the tailored-resume object route."""
    from deepagents.backends import CompositeBackend
    from deepagents.backends.filesystem import FilesystemBackend
    from deepagents.backends.utils import validate_path
    from obstore.store import MemoryStore

    from backend.agents.career_agent.object_backend import ObjectStoreBackend

    file_path = args.get("file_path")
    content = args.get("content")
    if not isinstance(file_path, str) or not isinstance(content, str):
        message = "write_file requires string file_path and content"
        raise TypeError(message)
    validated_path = validate_path(file_path)
    if not validated_path.startswith(_TAILORED_PREFIX) or not validated_path.endswith(
        (".yaml", ".yml")
    ):
        message = "tailored resume path must be /tailored_resume/*.yaml"
        raise ValueError(message)

    document = _resume_document(content)
    store = MemoryStore()
    with tempfile.TemporaryDirectory(prefix="nextrole-runledger-") as root:
        backend = CompositeBackend(
            default=FilesystemBackend(root_dir=root, virtual_mode=True),
            routes={
                _TAILORED_PREFIX: ObjectStoreBackend(
                    "tailored_resume",
                    store_factory=lambda: store,
                ),
            },
        )
        written = backend.write(validated_path, content)
    if written.error:
        raise RuntimeError(written.error)
    return {"path": written.path or validated_path, "document": document}


TOOLS = {
    "web_search": web_search,
    "write_file": write_file,
}
