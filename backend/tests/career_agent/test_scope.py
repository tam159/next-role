"""Per-user scope resolution (`backend.agents.career_agent.scope`).

`current_identity()` reads the run config; here we drive the pure derivations
(`kv_namespace`, `object_scope`) with explicit identities and assert the
single-user fallbacks match the historical layout.
"""

from types import SimpleNamespace
from unittest.mock import patch

from backend.agents.career_agent import scope as scope_mod
from backend.agents.career_agent.scope import (
    current_thread_id,
    current_user,
    kv_namespace,
    object_scope,
)


def test_object_scope_defaults_to_users_default() -> None:
    with patch.object(scope_mod, "current_identity", return_value=None):
        assert object_scope() == "users/default/career_agent"


def test_object_scope_uses_explicit_identity() -> None:
    assert object_scope("user-1") == "users/user-1/career_agent"


def test_kv_namespace_single_user_has_no_user_segment() -> None:
    # Byte-for-byte the pre-multi-user layout.
    assert kv_namespace("memory", identity=None) == ("career_agent", "memory")


def test_kv_namespace_prepends_identity_when_present() -> None:
    assert kv_namespace("memory", identity="user-1") == ("user-1", "career_agent", "memory")


def test_kv_namespace_resolves_identity_from_runtime_when_omitted() -> None:
    with patch.object(scope_mod, "current_identity", return_value="user-2"):
        assert kv_namespace("research") == ("user-2", "career_agent", "research")


def test_object_scope_resolves_identity_from_runtime_when_omitted() -> None:
    with patch.object(scope_mod, "current_identity", return_value="user-3"):
        assert object_scope() == "users/user-3/career_agent"


def test_current_identity_none_outside_runtime() -> None:
    # get_config() raises RuntimeError outside a runnable context.
    assert scope_mod.current_identity() is None


def test_current_identity_reads_langgraph_auth_user() -> None:
    class _User:
        identity = "user-9"

    fake_config = {"configurable": {"langgraph_auth_user": _User()}}
    with patch.object(scope_mod, "get_config", return_value=fake_config):
        assert scope_mod.current_identity() == "user-9"


def test_current_identity_falls_back_to_user_id_key() -> None:
    fake_config = {"configurable": {"langgraph_auth_user_id": "user-10"}}
    with patch.object(scope_mod, "get_config", return_value=fake_config):
        assert scope_mod.current_identity() == "user-10"


def test_current_identity_empty_string_is_unscoped() -> None:
    # Noop auth yields identity "" — must not scope.
    class _User:
        identity = ""

    fake_config = {"configurable": {"langgraph_auth_user": _User()}}
    with patch.object(scope_mod, "get_config", return_value=fake_config):
        assert scope_mod.current_identity() is None


# ---------------------------------------------------------------------------
# agent_root: a second agent's key space (see object_storage.AREA_ROOTS)
# ---------------------------------------------------------------------------


def test_object_scope_accepts_an_agent_root() -> None:
    assert object_scope("user-1", agent_root="analytics_agent") == "users/user-1/analytics_agent"


def test_object_scope_agent_root_keeps_single_user_fallback() -> None:
    with patch.object(scope_mod, "current_identity", return_value=None):
        assert object_scope(agent_root="analytics_agent") == "users/default/analytics_agent"


def test_kv_namespace_agent_root_single_user_has_no_user_segment() -> None:
    assert kv_namespace("large_tool_results", identity=None, agent_root="analytics_agent") == (
        "analytics_agent",
        "large_tool_results",
    )


def test_kv_namespace_agent_root_prepends_identity() -> None:
    assert kv_namespace("large_tool_results", identity="user-1", agent_root="analytics_agent") == (
        "user-1",
        "analytics_agent",
        "large_tool_results",
    )


# ---------------------------------------------------------------------------
# current_user / current_thread_id
# ---------------------------------------------------------------------------


def test_current_user_returns_the_configured_user() -> None:
    user = SimpleNamespace(identity="user-1", email="a@example.com")
    with patch.object(
        scope_mod,
        "get_config",
        return_value={"configurable": {"langgraph_auth_user": user}},
    ):
        assert current_user() is user


def test_current_user_is_none_outside_a_runnable_context() -> None:
    with patch.object(scope_mod, "get_config", side_effect=RuntimeError):
        assert current_user() is None


def test_current_user_is_none_without_custom_auth() -> None:
    with patch.object(scope_mod, "get_config", return_value={"configurable": {}}):
        assert current_user() is None


def test_current_thread_id_reads_the_run_config() -> None:
    with patch.object(scope_mod, "get_config", return_value={"configurable": {"thread_id": "t-1"}}):
        assert current_thread_id() == "t-1"


def test_current_thread_id_is_none_outside_a_runnable_context() -> None:
    with patch.object(scope_mod, "get_config", side_effect=RuntimeError):
        assert current_thread_id() is None


def test_current_thread_id_is_none_when_absent() -> None:
    with patch.object(scope_mod, "get_config", return_value={"configurable": {}}):
        assert current_thread_id() is None
