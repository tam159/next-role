"""Per-user scope resolution (`backend.agents.career_agent.scope`).

`current_identity()` reads the run config; here we drive the pure derivations
(`kv_namespace`, `object_scope`) with explicit identities and assert the
single-user fallbacks match the historical layout.
"""

from unittest.mock import patch

from backend.agents.career_agent import scope as scope_mod
from backend.agents.career_agent.scope import kv_namespace, object_scope


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
