"""Tools for the career agent."""

from pathlib import Path
from typing import Literal

from langchain_core.tools import tool

EXAMPLE_DIR: Path = Path(__file__).parent


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
