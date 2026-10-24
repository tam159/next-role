"""Web search / extract providers behind the career agent's web tools.

`web_search`, `web_extract` and `extract_jd` call `search` / `extract` /
`extract_one` here, which dispatch to Tavily or TinyFish per
`WEB_SEARCH_PROVIDER`. TinyFish responses are reshaped to Tavily's payload
shape (`results[].url/title/content|raw_content`) so the tool contract, the
hiring-recon skill and the frontend Sources parser stay provider-agnostic.

Both SDKs are imported lazily (also the patch seam for unit tests) and read
their key from env: `TAVILY_API_KEY` / `TINYFISH_API_KEY`.
"""

from typing import Any, Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

WebProvider = Literal["tavily", "tinyfish"]
SearchDepth = Literal["basic", "advanced", "fast", "ultra-fast"]
SearchTopic = Literal["general", "news", "finance"]
ExtractDepth = Literal["basic", "advanced"]
ContentFormat = Literal["markdown", "text"]

_PROVIDER_NAMES: dict[str, str] = {"tavily": "Tavily", "tinyfish": "TinyFish"}

# TinyFish Fetch accepts at most 10 URLs per request.
_TINYFISH_MAX_FETCH_URLS = 10


class WebSettings(BaseSettings):
    """`WEB_*` env config selecting the web research provider.

    Read per call (cheap) so a changed `.env` applies on the next backend
    restart without touching import-time state. Docs: README "Environment
    variables".
    """

    model_config = SettingsConfigDict(env_prefix="WEB_", extra="ignore")

    search_provider: WebProvider = "tavily"


def _provider() -> WebProvider:
    return WebSettings().search_provider


def search(
    query: str,
    *,
    search_depth: SearchDepth = "basic",
    topic: SearchTopic = "general",
    max_results: int = 5,
) -> dict[str, Any]:
    """Run a web search; returns `{query, results: [{url, title, content}]}`."""
    if _provider() == "tinyfish":
        return _tinyfish_search(query, topic=topic, max_results=max_results)

    from tavily import TavilyClient

    return TavilyClient().search(
        query=query,
        search_depth=search_depth,
        topic=topic,
        max_results=max_results,
    )


def extract(
    urls: list[str] | str,
    *,
    extract_depth: ExtractDepth = "basic",
    content_format: ContentFormat = "markdown",
) -> dict[str, Any]:
    """Extract pages; returns `{results: [{url, title, raw_content}], failed_results}`."""
    if _provider() == "tinyfish":
        return _tinyfish_extract([urls] if isinstance(urls, str) else list(urls))

    from tavily import TavilyClient

    return TavilyClient().extract(
        urls=urls,
        extract_depth=extract_depth,
        format=content_format,
    )


def extract_one(url: str) -> tuple[str, str]:
    """Extract a single URL as markdown and return `(title, raw_markdown)`.

    Raises on transport errors or empty results so callers can surface a
    consistent `Error: ...` message.
    """
    response = extract(url)
    results = response.get("results") or []
    if not results:
        failed = response.get("failed_results") or []
        detail = f": {failed[0].get('error')}" if failed and failed[0].get("error") else ""
        msg = f"{_PROVIDER_NAMES[_provider()]} returned no results for {url}{detail}"
        raise ValueError(msg)
    first = results[0]
    return first.get("title") or "", first.get("raw_content") or ""


def _tinyfish_client() -> Any:  # noqa: ANN401 - SDK client; typed as Any for the lazy-import test seam
    from tinyfish import TinyFish

    return TinyFish()


def _tinyfish_search(query: str, *, topic: SearchTopic, max_results: int) -> dict[str, Any]:
    # TinyFish has no `finance` domain and no result-count parameter: finance
    # falls back to plain web search, and results are trimmed client-side.
    domain_type = "news" if topic == "news" else "web"
    response = _tinyfish_client().search.query(query=query, domain_type=domain_type)
    return {
        "query": response.query,
        "results": [
            {"url": r.url, "title": r.title, "content": r.snippet}
            for r in response.results[: max(max_results, 0)]
        ],
    }


def _tinyfish_extract(urls: list[str]) -> dict[str, Any]:
    if len(urls) > _TINYFISH_MAX_FETCH_URLS:
        msg = f"TinyFish fetches at most {_TINYFISH_MAX_FETCH_URLS} URLs per call (got {len(urls)})"
        raise ValueError(msg)
    response = _tinyfish_client().fetch.get_contents(urls=urls, format="markdown")
    return {
        "results": [
            {"url": r.url, "title": r.title or "", "raw_content": r.text or ""}
            for r in response.results
        ],
        "failed_results": [{"url": e.url, "error": e.error} for e in response.errors],
    }
