"""Tools for the career agent."""

import re
from pathlib import Path
from typing import Any, Literal

from deepagents.backends import CompositeBackend
from deepagents.backends.protocol import WriteResult
from langchain_core.tools import BaseTool, tool

CAREER_AGENT_DIR: Path = Path(__file__).parent
UPLOAD_DIR: Path = CAREER_AGENT_DIR / "upload"

# LlamaParse emits `![alt](page_X_image_Y.jpg)` markdown for embedded images
# even when we don't extract them, which renders as broken-image 404s in the
# UI. The v2 ParsingCreateParams API has no flag to suppress these inline
# references — we strip them client-side, keeping the alt text.
_IMAGE_REF_RE = re.compile(r"!(\[[^\]]*\])\([^)]*\)")


def _strip_image_filenames(markdown: str) -> str:
    """Replace `![alt](filename)` with `[alt]` so broken refs don't 404 in the UI."""
    return _IMAGE_REF_RE.sub(r"\1", markdown)


@tool
def web_search(
    query: str,
    max_results: int = 5,
    topic: Literal["general", "news"] = "general",
) -> dict:
    """Search the web for current information.

    Args:
        query: The search query (be specific and detailed)
        max_results: Number of results to return (default: 5)
        topic: "general" for most queries, "news" for current events

    Returns:
        Search results with titles, URLs, and content excerpts.

    """
    try:
        from tavily import TavilyClient

        client = TavilyClient()
        return client.search(query, max_results=max_results, topic=topic)
    except Exception as e:
        return {"error": f"Search failed: {e}"}


def make_list_files(backend: CompositeBackend) -> BaseTool:
    """Build the `list_files` tool, closed over the agent's backend."""

    @tool
    def list_files(path: str) -> list[dict[str, Any]]:
        """List files in a directory with size and modification time, newest first.

        Use this when you need recency or size info, or want results ordered by
        modification time. For a quick path-only listing, prefer the built-in `ls`.

        Works on any path the agent can reach — `/upload/`, `/processed/`,
        `/research/`, `/interview_prep/`, `/workspace/`, etc.

        Args:
            path: Absolute directory path, e.g. "/upload/".

        Returns:
            List of `{path, is_dir, size, modified_at}` entries sorted by
            `modified_at` descending. On error, returns `[{"error": "..."}]`.

        """
        try:
            result = backend.ls(path)
        except Exception as e:
            return [{"error": f"ls failed: {e}"}]
        if result.error:
            return [{"error": result.error}]
        entries = [dict(e) for e in result.entries or []]
        return sorted(entries, key=lambda e: e.get("modified_at", ""), reverse=True)

    return list_files


def _upsert(backend: CompositeBackend, path: str, content: str) -> WriteResult:
    """Write `content` to `path`, replacing any existing file at the same path.

    Why this wrapper exists: deepagents' `BackendProtocol.write()` refuses to
    overwrite by design (it expects a "read-then-edit" workflow). For the
    parse_document flow we want overwrite — re-uploading the same CV/JD
    should refresh the parsed copy. There is no `delete` API on the
    BackendProtocol, so we implement upsert via two public-API calls:
    try `write`; on the already-exists error, fall back to `edit` with the
    current content as `old_string`.
    """
    res = backend.write(path, content)
    if not res.error:
        return res

    read_res = backend.read(path, offset=0, limit=10**9)
    if read_res.error or not read_res.file_data:
        return res

    existing = read_res.file_data.get("content", "")
    if isinstance(existing, list):
        existing = "\n".join(existing)
    if existing == content:
        return WriteResult(path=path)
    if not existing:
        return WriteResult(error=f"{path} exists but is empty; cannot overwrite via edit")

    edit_res = backend.edit(path, old_string=existing, new_string=content)
    if edit_res.error:
        return WriteResult(error=f"Overwrite failed for {path}: {edit_res.error}")
    return WriteResult(path=path)


def make_parse_document(backend: CompositeBackend) -> BaseTool:
    """Build the `parse_document` tool, closed over the agent's backend."""

    @tool
    def parse_document(filename: str, save_as: str) -> str:
        """Parse a document from /upload/ with LlamaParse and persist as markdown.

        The parsed markdown is written directly to `/processed/<save_as>.md`
        — you do NOT need to call `write_file` afterwards.

        Args:
            filename: Basename of a file in `/upload/`, e.g.
                "Resume - Lead AI_ML - Tam NGUYEN.pdf". Path separators are
                rejected.
            save_as: Kebab-case slug WITHOUT extension. The tool appends `.md`.
                Pick something content-meaningful — for a CV, candidate name
                + role (e.g. "tam-nguyen-lead-ai-ml-resume"); for a JD,
                company + role (e.g. "aws-ai-solution-engineer-jd").

        Returns:
            Short confirmation string with the saved path and markdown length,
            or `Error: ...` on failure.

        """
        from llama_cloud import LlamaCloud

        src = UPLOAD_DIR / filename
        if Path(filename).name != filename or not src.is_file():
            return f"Error: file not found at /upload/{filename}"

        dest = f"/processed/{save_as}.md"
        try:
            client = LlamaCloud()
            file_obj = client.files.create(file=str(src), purpose="parse")
            result = client.parsing.parse(
                file_id=file_obj.id,
                tier="agentic",
                version="latest",
                disable_cache=False,
                expand=["markdown_full"],
                output_options={
                    "markdown": {"annotate_links": True},
                    "images_to_save": [],
                },
                processing_options={"cost_optimizer": {"enable": True}},
            )
            markdown = getattr(result, "markdown_full", None) or ""
            if not markdown:
                return f"Error: LlamaParse returned no markdown for {filename}"
            markdown = _strip_image_filenames(markdown)
            write_result = _upsert(backend, dest, markdown)
            if write_result.error:
                return f"Error writing {dest}: {write_result.error}"
        except Exception as e:
            return f"Error processing {filename}: {e}"
        return f"Saved {dest} ({len(markdown)} chars)"

    return parse_document
