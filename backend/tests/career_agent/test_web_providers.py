"""Unit tests for the web search / extract provider switch.

Both SDKs are mocked — tests never hit the network or burn Tavily / TinyFish
credits.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from pydantic import ValidationError


@pytest.fixture
def tavily_env(monkeypatch):
    monkeypatch.setenv("WEB_SEARCH_PROVIDER", "tavily")


@pytest.fixture
def tinyfish_env(monkeypatch):
    monkeypatch.setenv("WEB_SEARCH_PROVIDER", "tinyfish")


class _FakeTavily:
    def __init__(self):
        self.search_kwargs = None
        self.extract_kwargs = None

    def search(self, **kwargs):
        self.search_kwargs = kwargs
        return {"query": kwargs["query"], "results": [{"url": "https://t.com", "title": "T"}]}

    def extract(self, **kwargs):
        self.extract_kwargs = kwargs
        return {"results": [{"url": "https://t.com", "title": "T", "raw_content": "body"}]}


def _fake_tinyfish(*, search_results=(), fetch_results=(), fetch_errors=()):
    calls: dict = {}

    def query(**kwargs):
        calls["search"] = kwargs
        return SimpleNamespace(query=kwargs["query"], results=list(search_results))

    def get_contents(**kwargs):
        calls["fetch"] = kwargs
        return SimpleNamespace(results=list(fetch_results), errors=list(fetch_errors))

    client = SimpleNamespace(
        search=SimpleNamespace(query=query),
        fetch=SimpleNamespace(get_contents=get_contents),
    )
    return client, calls


def _search_hit(n: int):
    return SimpleNamespace(
        url=f"https://e.com/{n}",
        title=f"Hit {n}",
        snippet=f"snippet {n}",
        site_name="e.com",
        position=n,
    )


# ---------------------------------------------------------------------------
# settings
# ---------------------------------------------------------------------------


def test_provider_defaults_to_tavily(monkeypatch):
    from backend.agents.career_agent.web_providers import WebSettings

    monkeypatch.delenv("WEB_SEARCH_PROVIDER", raising=False)
    assert WebSettings().search_provider == "tavily"


def test_provider_rejects_unknown_value(monkeypatch):
    from backend.agents.career_agent.web_providers import WebSettings

    monkeypatch.setenv("WEB_SEARCH_PROVIDER", "bing")
    with pytest.raises(ValidationError):
        WebSettings()


# ---------------------------------------------------------------------------
# tavily
# ---------------------------------------------------------------------------


def test_tavily_search_passes_arguments_through(tavily_env):
    from backend.agents.career_agent import web_providers

    fake = _FakeTavily()
    with patch("tavily.TavilyClient", return_value=fake):
        result = web_providers.search("acme", search_depth="advanced", topic="news", max_results=3)

    assert fake.search_kwargs == {
        "query": "acme",
        "search_depth": "advanced",
        "topic": "news",
        "max_results": 3,
    }
    assert result["results"][0]["url"] == "https://t.com"


def test_tavily_extract_passes_arguments_through(tavily_env):
    from backend.agents.career_agent import web_providers

    fake = _FakeTavily()
    with patch("tavily.TavilyClient", return_value=fake):
        web_providers.extract(["https://t.com"], extract_depth="advanced", content_format="text")

    assert fake.extract_kwargs == {
        "urls": ["https://t.com"],
        "extract_depth": "advanced",
        "format": "text",
    }


# ---------------------------------------------------------------------------
# tinyfish
# ---------------------------------------------------------------------------


def test_tinyfish_search_normalizes_to_tavily_shape(tinyfish_env):
    from backend.agents.career_agent import web_providers

    client, calls = _fake_tinyfish(search_results=[_search_hit(1), _search_hit(2)])
    with patch("tinyfish.TinyFish", return_value=client):
        result = web_providers.search("acme hiring")

    assert calls["search"] == {"query": "acme hiring", "domain_type": "web"}
    assert result == {
        "query": "acme hiring",
        "results": [
            {"url": "https://e.com/1", "title": "Hit 1", "content": "snippet 1"},
            {"url": "https://e.com/2", "title": "Hit 2", "content": "snippet 2"},
        ],
    }


def test_tinyfish_search_trims_to_max_results(tinyfish_env):
    from backend.agents.career_agent import web_providers

    client, _ = _fake_tinyfish(search_results=[_search_hit(n) for n in range(10)])
    with patch("tinyfish.TinyFish", return_value=client):
        result = web_providers.search("acme", max_results=3)

    assert [r["url"] for r in result["results"]] == [f"https://e.com/{n}" for n in range(3)]


@pytest.mark.parametrize(
    ("topic", "domain_type"),
    [("general", "web"), ("news", "news"), ("finance", "web")],
)
def test_tinyfish_search_maps_topic_to_domain_type(tinyfish_env, topic, domain_type):
    from backend.agents.career_agent import web_providers

    client, calls = _fake_tinyfish()
    with patch("tinyfish.TinyFish", return_value=client):
        web_providers.search("acme", topic=topic)

    assert calls["search"]["domain_type"] == domain_type


def test_tinyfish_extract_normalizes_results_and_errors(tinyfish_env):
    from backend.agents.career_agent import web_providers

    client, calls = _fake_tinyfish(
        fetch_results=[SimpleNamespace(url="https://e.com/a", title=None, text="# A")],
        fetch_errors=[SimpleNamespace(url="https://e.com/b", error="timeout")],
    )
    with patch("tinyfish.TinyFish", return_value=client):
        result = web_providers.extract("https://e.com/a")

    assert calls["fetch"] == {"urls": ["https://e.com/a"], "format": "markdown"}
    assert result == {
        "results": [{"url": "https://e.com/a", "title": "", "raw_content": "# A"}],
        "failed_results": [{"url": "https://e.com/b", "error": "timeout"}],
    }


def test_tinyfish_extract_rejects_more_than_ten_urls(tinyfish_env):
    from backend.agents.career_agent import web_providers

    urls = [f"https://e.com/{n}" for n in range(11)]
    with pytest.raises(ValueError, match="at most 10"):
        web_providers.extract(urls)


def test_extract_one_surfaces_tinyfish_fetch_error(tinyfish_env):
    from backend.agents.career_agent import web_providers

    client, _ = _fake_tinyfish(
        fetch_errors=[SimpleNamespace(url="https://e.com/jd", error="anti_bot_blocked")],
    )
    with (
        patch("tinyfish.TinyFish", return_value=client),
        pytest.raises(ValueError, match=r"TinyFish returned no results.*anti_bot_blocked"),
    ):
        web_providers.extract_one("https://e.com/jd")


# ---------------------------------------------------------------------------
# tools wiring
# ---------------------------------------------------------------------------


def test_web_search_tool_uses_tinyfish_when_selected(tinyfish_env):
    from backend.agents.career_agent.tools import web_search

    client, _ = _fake_tinyfish(search_results=[_search_hit(1)])
    with patch("tinyfish.TinyFish", return_value=client):
        result = web_search.invoke({"query": "acme"})

    assert result["results"][0]["content"] == "snippet 1"


def test_web_extract_tool_returns_error_dict_on_failure(tinyfish_env):
    from backend.agents.career_agent.tools import web_extract

    with patch("tinyfish.TinyFish", side_effect=ValueError("TINYFISH_API_KEY is not set")):
        result = web_extract.invoke({"urls": "https://e.com"})

    assert result == {"error": "Extract failed: TINYFISH_API_KEY is not set"}
