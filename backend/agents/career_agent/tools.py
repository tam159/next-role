"""Tools for the career agent."""

import datetime
import json
import re
import shlex
import tempfile
import textwrap
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from deepagents.backends import CompositeBackend
from deepagents.backends.protocol import WriteResult
from langchain_core.tools import BaseTool, tool

CAREER_AGENT_DIR: Path = Path(__file__).parent

# Top-level YAML key marking the rendercv `settings:` block. Used by
# `render_resume_pdf` to strip any trailing settings block from the stored
# YAML before hydrating its scratch render copy (keeps the durable artifact
# free of machine-local paths). Anchored to start-of-line in MULTILINE mode.
_SETTINGS_BLOCK_HEADER_RE = re.compile(r"^settings:", re.MULTILINE)

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
            # A missing directory is "no files yet" for our usage (e.g. a fresh
            # user with nothing under /upload/), not an error. deepagents >=0.6.x
            # reports this via error="Path '<path>': path_not_found" instead of
            # the empty listing older versions returned, so normalize it back.
            if result.error.endswith("path_not_found"):
                return []
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

        Parent directories are created automatically — do NOT run `mkdir`
        (or any other shell command) to create them first.

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


def _materialize_source(backend: CompositeBackend, source_path: str) -> Path | str:
    """Download `source_path` through the backend into a temp file for LlamaCloud.

    LlamaCloud's SDK uploads a real on-disk file (and infers the document type
    from its filename suffix), while artifact bytes live in object storage —
    so the file is materialized into a `NamedTemporaryFile` that preserves the
    original suffix. Returns the temp `Path`, or an `Error: ...` string.
    The caller must delete the returned file when done.
    """
    if not source_path.startswith("/"):
        return (
            f"Error: invalid source_path {source_path!r} (must be absolute, e.g. /upload/foo.pdf)"
        )
    if ".." in Path(source_path).parts:
        return f"Error: invalid source_path {source_path!r} (path traversal not allowed)"
    responses = backend.download_files([source_path])
    resp = responses[0] if responses else None
    if resp is None or resp.error or resp.content is None:
        detail = resp.error if resp is not None and resp.error else "not found"
        return f"Error: cannot read {source_path} ({detail})"
    tmp = tempfile.NamedTemporaryFile(  # noqa: SIM115 — handed off, deleted by caller
        delete=False,
        prefix="nextrole-parse-",
        suffix=Path(source_path).suffix,
    )
    try:
        tmp.write(resp.content)
    finally:
        tmp.close()
    return Path(tmp.name)


def make_parse_document(backend: CompositeBackend) -> BaseTool:
    """Build the `parse_document` tool, closed over the agent's backend."""

    @tool
    def parse_document(source_path: str, output_path: str) -> str:
        """Parse a document with LlamaParse and persist the result as markdown.

        Works on any document the agent's filesystem can read: PDFs, DOCX,
        PPTX, images, etc. Common source dir is `/upload/` (user uploads),
        but any readable backend path is supported. The parsed markdown is
        written directly to `output_path` — you do NOT need to call
        `write_file` afterwards.

        Args:
            source_path: Absolute backend path to the document to parse, e.g.
                "/upload/Resume - Tam NGUYEN.pdf" or
                "/workspace/spec.docx". Path traversal (`..`) is rejected.
            output_path: Absolute backend path where the parsed markdown will
                be saved, e.g. "/processed/tam-nguyen-lead-ai-ml-resume.md".
                Must end with `.md`. Pick a content-meaningful filename.

        Returns:
            Short confirmation string with the saved path and markdown length,
            or `Error: ...` on failure.

        """
        from llama_cloud import LlamaCloud

        if not output_path.startswith("/") or not output_path.endswith(".md"):
            return (
                f"Error: invalid output_path {output_path!r} "
                "(must be an absolute path ending in .md)"
            )

        resolved = _materialize_source(backend, source_path)
        if isinstance(resolved, str):
            return resolved
        src = resolved

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
        finally:
            src.unlink(missing_ok=True)
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


class _RenderStageError(Exception):
    """In-band render-pipeline failure; the message is the tool's error result."""


def _coerce_string_entry(item: object) -> str | None:
    """Coerce a should-be-string YAML list entry back to a string, if possible.

    The LLM's most common rendercv defect is an unquoted `: ` inside a bullet
    ("Strongest where AI meets execution: reusable patterns…"), which YAML
    silently parses as a one-pair mapping — rendercv then fails with "Input
    should be a valid string". A trailing colon parses as `{text: None}`, and
    bare numbers parse as ints. All three have an exact, lossless textual
    inverse, so they are repaired here instead of burning an LLM roundtrip.

    Returns the repaired string, or `None` when the entry is either already
    valid or not mechanically repairable (nested lists, multi-key mappings —
    those stay with the agent's fix-and-retry loop).
    """
    # bool is an int subclass, and `Answer: yes` → True has no faithful
    # textual inverse — leave booleans to the agent.
    if isinstance(item, bool):
        return None
    if isinstance(item, (int, float)):
        return str(item)
    if not isinstance(item, dict) or len(item) != 1:
        return None
    key, value = next(iter(item.items()))
    if not isinstance(key, str) or isinstance(value, bool):
        return None
    if value is None or isinstance(value, (str, int, float, datetime.date)):
        return f"{key}:" if value is None else f"{key}: {value}"
    return None


def _repair_string_entries(items: object) -> int:
    """Repair should-be-string entries of a list in place; returns fix count."""
    if not isinstance(items, list):
        return 0
    entries: list[Any] = items
    fixes = 0
    for i, item in enumerate(entries):
        coerced = _coerce_string_entry(item)
        if coerced is not None:
            entries[i] = coerced
            fixes += 1
    return fixes


def _normalize_resume_yaml(body: str) -> tuple[str, int]:
    """Repair mechanically-fixable string entries in a rendercv YAML body.

    Walks `cv.sections`: items of text-style sections (lists of strings) and
    every entry's `highlights` list. Returns `(body, fix_count)` — the body is
    re-serialized only when something was repaired, so the untouched original
    (comments included) flows through in the common case. Unparseable YAML is
    returned as-is; rendercv will surface the parse error for the agent loop.
    """
    import yaml

    try:
        tree = yaml.safe_load(body)
    except yaml.YAMLError:
        return body, 0
    if not isinstance(tree, dict):
        return body, 0
    cv = tree.get("cv")
    sections = cv.get("sections") if isinstance(cv, dict) else None
    if not isinstance(sections, dict):
        return body, 0

    fixes = 0
    for section in sections.values():
        fixes += _repair_string_entries(section)
        if isinstance(section, list):
            for entry in section:
                if isinstance(entry, dict):
                    fixes += _repair_string_entries(entry.get("highlights"))

    if fixes == 0:
        return body, 0
    return yaml.safe_dump(tree, sort_keys=False, allow_unicode=True, width=4096), fixes


def _read_resume_yaml_body(backend: CompositeBackend, yaml_path: str) -> str:
    """Read the stored YAML and strip any trailing `settings:` block.

    Pre-migration YAMLs carried a settings block with machine-absolute paths;
    the stored copy must stay clean, so it is stripped before hydration.
    """
    read_res = backend.read(yaml_path, offset=0, limit=10**9)
    if read_res.error or not read_res.file_data:
        msg = f"Error (read): {yaml_path}: {read_res.error or 'not found'}"
        raise _RenderStageError(msg)
    existing = read_res.file_data.get("content", "")
    if isinstance(existing, list):
        existing = "\n".join(existing)
    if not existing.strip():
        msg = f"Error (read): {yaml_path} is empty"
        raise _RenderStageError(msg)
    match = _SETTINGS_BLOCK_HEADER_RE.search(existing)
    body = existing[: match.start()] if match else existing
    return body.rstrip() + "\n"


def _write_render_copy(body: str, render_dir: Path, stem: str) -> tuple[Path, int]:
    """Write the normalized render copy + settings block into the temp render dir.

    Renders happen in a throwaway `TemporaryDirectory` outside the repo tree —
    rendercv is a subprocess that needs a real filesystem, but none of its
    working files are artifacts: the durable YAML/PDF/typ all live in the
    object store. The settings block pins every rendercv output inside the
    temp dir; its machine-local absolute paths are never persisted anywhere.

    The render copy is also normalized: mechanically-repairable string-entry
    defects (unquoted mid-string colons, trailing colons, bare numbers) are
    fixed here so they never cost an LLM fix-and-retry roundtrip. The stored
    YAML keeps the agent's original text.
    """
    body, fixes = _normalize_resume_yaml(body)
    settings_block = textwrap.dedent(
        f"""\
        settings:
          current_date: today
          render_command:
            output_folder: {render_dir}
            typst_path: {render_dir / stem}.typ
            pdf_path: OUTPUT_FOLDER/{stem}.pdf
            dont_generate_markdown: true
            dont_generate_html: true
            dont_generate_png: true
            dont_generate_typst: false
            dont_generate_pdf: false
          bold_keywords: []
        """,
    )
    yaml_file = render_dir / f"{stem}.yaml"
    yaml_file.write_text(body + settings_block, encoding="utf-8")
    return yaml_file, fixes


def _run_rendercv(backend: CompositeBackend, yaml_file: Path) -> str:
    """Run `rendercv render` on the temp render copy via the composite default.

    The path is a real absolute path outside the shell backend's root, so the
    virtual-path rewriter passes it through untouched. Returns the process
    output (kept for the verify-stage message).
    """
    try:
        exec_res = backend.execute(f"rendercv render {shlex.quote(str(yaml_file))}")
    except NotImplementedError:
        msg = "Error (render): the agent backend does not support shell execution"
        raise _RenderStageError(msg) from None
    if exec_res.exit_code not in (0, None):
        msg = f"Error (render): rendercv exited {exec_res.exit_code}:\n{exec_res.output[-6000:]}"
        raise _RenderStageError(msg)
    return exec_res.output


def _collect_render_outputs(render_dir: Path, stem: str, exec_output: str) -> dict[str, bytes]:
    """Read rendercv's outputs from the temp dir, keyed by suffix.

    The `.pdf` is mandatory (verify-stage error when missing); the `.typ`
    typesetting intermediate is included when present.
    """
    pdf_file = render_dir / f"{stem}.pdf"
    if not pdf_file.is_file():
        msg = (
            f"Error (verify): rendercv reported success but {stem}.pdf was not "
            f"produced. Output tail:\n{exec_output[-2000:]}"
        )
        raise _RenderStageError(msg)
    outputs = {".pdf": pdf_file.read_bytes()}
    typ_file = render_dir / f"{stem}.typ"
    if typ_file.is_file():
        outputs[".typ"] = typ_file.read_bytes()
    return outputs


def _publish_render_outputs(
    backend: CompositeBackend,
    yaml_path: str,
    outputs: dict[str, bytes],
) -> None:
    """Upload the rendered files to the artifact store next to their YAML."""
    files = [
        (str(Path(yaml_path).with_suffix(suffix)), content) for suffix, content in outputs.items()
    ]
    responses = backend.upload_files(files)
    failed = [r.path for r in responses if r.error] if responses else [p for p, _ in files]
    if failed:
        msg = (
            f"Error (publish): rendered OK but saving {', '.join(failed)} failed. "
            "Fix storage and call this tool again (it re-renders from the stored YAML)."
        )
        raise _RenderStageError(msg)


def make_render_resume_pdf(backend: CompositeBackend) -> BaseTool:
    """Build the `render_resume_pdf` tool, closed over the agent's backend."""

    @tool
    def render_resume_pdf(yaml_path: str) -> str:
        """Render a tailored-resume YAML to PDF and publish it next to the YAML.

        One call runs the whole pipeline: read the YAML from the artifact
        store, write a render copy (with the canonical rendercv `settings:`
        block) into a throwaway temp directory, run `rendercv render` on it,
        and publish the outputs back next to the YAML. Run it AFTER writing
        the YAML (`cv:`, `design:`, `locale:`).

        Do NOT append a `settings:` block to the YAML and do NOT run
        `rendercv` via `execute` — the stored YAML stays settings-free; the
        canonical block (with machine-local temp paths) exists only in the
        render copy.

        On a rendercv failure the result contains its output — fix the YAML
        (`edit_file`/`overwrite_file`) and call this tool again. Idempotent:
        re-rendering overwrites the previous outputs.

        Args:
            yaml_path: Absolute backend path, e.g.
                "/tailored_resume/<resume-slug>/<jd-slug>.yaml". Must live under
                "/tailored_resume/" and end in ".yaml" or ".yml".

        Returns:
            Short confirmation string with the published PDF path, or
            `Error (<stage>): ...` naming the failed pipeline stage.

        """
        if not yaml_path.startswith("/tailored_resume/") or not yaml_path.endswith(
            (".yaml", ".yml"),
        ):
            return (
                f"Error: invalid yaml_path {yaml_path!r} "
                "(must start with /tailored_resume/ and end with .yaml/.yml)"
            )
        if ".." in Path(yaml_path).parts:
            return f"Error: invalid yaml_path {yaml_path!r} (path traversal not allowed)"

        pdf_dest = str(Path(yaml_path).with_suffix(".pdf"))
        try:
            body = _read_resume_yaml_body(backend, yaml_path)
            with tempfile.TemporaryDirectory(prefix="nextrole-render-") as tmp:
                render_dir = Path(tmp)
                stem = Path(yaml_path).stem
                yaml_file, fixes = _write_render_copy(body, render_dir, stem)
                exec_output = _run_rendercv(backend, yaml_file)
                outputs = _collect_render_outputs(render_dir, stem, exec_output)
            _publish_render_outputs(backend, yaml_path, outputs)
        except _RenderStageError as e:
            return str(e)
        note = (
            f" (auto-repaired {fixes} invalid string entr{'y' if fixes == 1 else 'ies'} — "
            "unquoted colons or bare numbers; quote such strings next time)"
            if fixes
            else ""
        )
        return f"Rendered and published {pdf_dest}{note}"

    return render_resume_pdf


def _weasyprint_pdf_bytes(html_str: str, template_dir: Path) -> bytes:
    """Render HTML to PDF bytes via weasyprint; raises on empty output.

    weasyprint is imported lazily — it pulls Pango/GLib via cffi at import
    time, so deferring keeps `tools.py` import-safe on hosts without those
    native libs.
    """
    from weasyprint import HTML

    pdf_bytes = HTML(string=html_str, base_url=str(template_dir)).write_pdf()
    if not pdf_bytes:
        msg = "weasyprint produced no output"
        raise ValueError(msg)
    return pdf_bytes


def make_render_battlecard_pdf(backend: CompositeBackend) -> BaseTool:
    """Build the `render_battlecard_pdf` tool, closed over the agent's backend."""

    @tool
    def render_battlecard_pdf(json_path: str) -> str:
        """Render an interview-battlecard JSON to PDF via weasyprint.

        Run this AFTER the agent has written the JSON source of truth.
        Reads the JSON via the backend, renders the bundled Jinja2 template,
        and saves `<stem>.pdf` next to the JSON in the artifact store.
        Idempotent — any prior PDF at the same path is overwritten.

        Args:
            json_path: Absolute backend path under `/interview_battlecard/`
                ending in `.json`, e.g.
                "/interview_battlecard/<resume-slug>/<jd-slug>.json".

        Returns:
            Short confirmation string with the saved PDF path,
            or `Error: ...` on failure.

        """
        if not json_path.startswith("/interview_battlecard/") or not json_path.endswith(".json"):
            return (
                f"Error: invalid json_path {json_path!r} "
                "(must start with /interview_battlecard/ and end with .json)"
            )

        read_res = backend.read(json_path, offset=0, limit=10**9)
        if read_res.error or not read_res.file_data:
            return f"Error reading {json_path}: {read_res.error or 'not found'}"
        content = read_res.file_data.get("content", "")
        if isinstance(content, list):
            content = "\n".join(content)
        try:
            data = json.loads(content)
        except json.JSONDecodeError as e:
            return f"Error: {json_path} is not valid JSON: {e}"

        from jinja2 import Environment, FileSystemLoader

        template_dir = CAREER_AGENT_DIR / "templates" / "battlecard"
        env = Environment(loader=FileSystemLoader(str(template_dir)), autoescape=True)
        template = env.get_template("battlecard.html.j2")
        html_str = template.render(**data)

        try:
            pdf_bytes = _weasyprint_pdf_bytes(html_str, template_dir)
        except Exception as e:
            return f"Error rendering PDF for {json_path}: {e}"

        # Publish through the backend so the PDF lands in the artifact store
        # (object storage) next to its JSON, not on local disk.
        pdf_backend_path = json_path.removesuffix(".json") + ".pdf"
        responses = backend.upload_files([(pdf_backend_path, pdf_bytes)])
        if not responses or responses[0].error:
            detail = responses[0].error if responses else "no response"
            return f"Error saving PDF {pdf_backend_path}: {detail}"
        return f"Rendered PDF to {pdf_backend_path}"

    return render_battlecard_pdf


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
