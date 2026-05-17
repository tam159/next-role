"""Tools for the career agent."""

import re
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from deepagents.backends import CompositeBackend
from deepagents.backends.protocol import WriteResult
from langchain_core.tools import BaseTool, tool

CAREER_AGENT_DIR: Path = Path(__file__).parent

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
    search_depth: Literal["basic", "advanced", "fast", "ultra-fast"] = "basic",
    topic: Literal["general", "news", "finance"] = "general",
    max_results: int = 5,
) -> dict:
    """Search the web for current information.

    Args:
        query: The search query (be specific and detailed)
        search_depth: The depth of the search. advanced search is tailored to retrieve the most
            relevant sources and content snippets for your query, while basic search provides
            generic content snippets from each source
        topic: "general" for most queries, "news" for current events
        max_results: Number of results to return

    Returns:
        Search results with url, title, content and score

    """
    try:
        from tavily import TavilyClient

        client = TavilyClient()
        return client.search(
            query=query,
            search_depth=search_depth,
            topic=topic,
            max_results=max_results,
        )
    except Exception as e:
        return {"error": f"Search failed: {e}"}


@tool
def web_extract(
    urls: list[str] | str,
    extract_depth: Literal["basic", "advanced"] = "basic",
    content_format: Literal["markdown", "text"] = "markdown",
) -> dict:
    """Search the web for current information.

    Args:
    urls: The URLs to extract content from
    extract_depth: The depth of the extraction process. advanced extraction
        retrieves more data, including tables and embedded content, with higher
        success but may increase latency.
    content_format: The format of the extracted content

    Returns:
        Search results with url, title, raw_content and images

    """
    try:
        from tavily import TavilyClient

        client = TavilyClient()
        return client.extract(
            urls=urls,
            extract_depth=extract_depth,
            format=content_format,
        )
    except Exception as e:
        return {"error": f"Extract failed: {e}"}


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


def make_overwrite_file(backend: CompositeBackend) -> BaseTool:
    """Build the `overwrite_file` tool, closed over the agent's backend."""

    @tool
    def overwrite_file(file_path: str, new_content: str) -> str:
        """Replace the entire contents of a file (or create it if missing).

        Use this when:
        - You need to fully replace the body of an existing file.
        - You need to write a file at a path that may or may not already exist
          and you don't care which.

        Prefer `edit_file` for small targeted edits or appends to existing
        files where a unique anchor substring is enough — those are cheaper
        and safer than a full overwrite.
        Prefer `write_file` when you want to create a new file.

        Args:
            file_path: Absolute path.
            new_content: The new full body of the file.

        Returns:
            Short confirmation string with the saved path and content length,
            or `Error: ...` on failure.

        """
        result = _upsert(backend, file_path, new_content)
        if result.error:
            return f"Error overwriting {file_path}: {result.error}"
        return f"Saved {file_path} ({len(new_content)} chars)"

    return overwrite_file


def _resolve_source_on_disk(source_path: str) -> Path | str:
    """Resolve a backend absolute path to a real on-disk file under `CAREER_AGENT_DIR`.

    Returns a `Path` if the file exists on disk, or a string error message
    suitable for returning to the caller. LlamaCloud needs a real file on disk
    — paths backed by `StoreBackend` routes (e.g. `/processed/`, `/research/`)
    are not on disk and will be rejected here.
    """
    if not source_path.startswith("/"):
        return (
            f"Error: invalid source_path {source_path!r} (must be absolute, e.g. /upload/foo.pdf)"
        )
    if ".." in Path(source_path).parts:
        return f"Error: invalid source_path {source_path!r} (path traversal not allowed)"
    src = CAREER_AGENT_DIR / source_path.lstrip("/")
    if not src.is_file():
        return f"Error: file not found at {source_path}"
    return src


def make_parse_document(backend: CompositeBackend) -> BaseTool:
    """Build the `parse_document` tool, closed over the agent's backend."""

    @tool
    def parse_document(source_path: str, output_path: str) -> str:
        """Parse a document with LlamaParse and persist the result as markdown.

        Works on any document the agent can read from disk: PDFs, DOCX, PPTX,
        images, etc. Common source dir is `/upload/` (user uploads), but any
        on-disk file under the agent's workspace is supported. The parsed
        markdown is written directly to `output_path` — you do NOT need to
        call `write_file` afterwards.

        Args:
            source_path: Absolute backend path to the document to parse, e.g.
                "/upload/Resume - Tam NGUYEN.pdf" or
                "/workspace/spec.docx". Must point to a real on-disk file.
                Path traversal (`..`) is rejected.
            output_path: Absolute backend path where the parsed markdown will
                be saved, e.g. "/processed/tam-nguyen-lead-ai-ml-resume.md".
                Must end with `.md`. Pick a content-meaningful filename.

        Returns:
            Short confirmation string with the saved path and markdown length,
            or `Error: ...` on failure.

        """
        from llama_cloud import LlamaCloud

        resolved = _resolve_source_on_disk(source_path)
        if isinstance(resolved, str):
            return resolved
        src = resolved

        if not output_path.startswith("/") or not output_path.endswith(".md"):
            return (
                f"Error: invalid output_path {output_path!r} "
                "(must be an absolute path ending in .md)"
            )

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
                return f"Error: LlamaParse returned no markdown for {source_path}"
            markdown = _strip_image_filenames(markdown)
            write_result = _upsert(backend, output_path, markdown)
            if write_result.error:
                return f"Error writing {output_path}: {write_result.error}"
        except Exception as e:
            return f"Error processing {source_path}: {e}"
        return f"Saved {output_path} ({len(markdown)} chars)"

    return parse_document


def _tavily_extract_one(url: str) -> tuple[str, str]:
    """Extract a single URL via Tavily and return `(title, raw_markdown)`.

    Raises on transport errors or empty results so callers can surface a
    consistent `Error: ...` message.
    """
    from tavily import TavilyClient

    client = TavilyClient()
    response = client.extract(urls=url, extract_depth="basic", format="markdown")
    results = response.get("results") or []
    if not results:
        msg = f"Tavily returned no results for {url}"
        raise ValueError(msg)
    first = results[0]
    return first.get("title") or "", first.get("raw_content") or ""


def make_extract_jd(backend: CompositeBackend) -> BaseTool:
    """Build the `extract_jd` tool, closed over the agent's backend."""

    @tool
    def extract_jd(url: str, save_as: str) -> str:
        """Extract a JD from a URL via Tavily and persist as markdown.

        The extracted markdown is written directly to `/processed/<save_as>.md`
        — you do NOT need to call `write_file` afterwards. Use this when the
        user gives a JD as a URL instead of a file upload (for uploads, use
        `parse_document`).

        Args:
            url: Full http(s) URL to the JD page.
            save_as: Kebab-case slug WITHOUT extension. The tool appends `.md`.
                Use company + role (e.g. "amazon-senior-ai-solution-architect-jd").

        Returns:
            Short confirmation string with the saved path and markdown length,
            or `Error: ...` on failure.

        """
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return f"Error: invalid url {url!r} (must be http(s)://...)"
        if (
            not save_as
            or any(c in save_as for c in ("/", "\\"))
            or ".." in save_as
            or "." in save_as
        ):
            return (
                f"Error: invalid save_as {save_as!r} "
                "(use a kebab-case slug, no path separators or extension)"
            )

        dest = f"/processed/{save_as}.md"
        try:
            title, raw_markdown = _tavily_extract_one(url)
            body = _strip_image_filenames(raw_markdown)
            header = f"# {title}\n\n" if title else ""
            content = f"{header}_Source: {url}_\n\n{body}"
            write_result = _upsert(backend, dest, content)
            if write_result.error:
                return f"Error writing {dest}: {write_result.error}"
        except Exception as e:
            return f"Error extracting {url}: {e}"
        return f"Saved {dest} ({len(content)} chars from {url})"

    return extract_jd
