"""Who may run the analytics agent.

The agent reads every user's activity, so access is an explicit allowlist. The
two properties that matter: single-user deployments are unaffected, and an
unset allowlist under auth denies rather than admits.
"""

import pytest
from backend.agents.analytics_agent.access import REFUSAL, auth_enabled, is_allowed
from backend.agents.analytics_agent.settings import AccessSettings


def _settings(allowed: str) -> AccessSettings:
    return AccessSettings(allowed_users=allowed)


# ---------------------------------------------------------------------------
# single-user mode
# ---------------------------------------------------------------------------


def test_single_user_mode_allows_the_operator():
    # There is one operator and no login; the allowlist is not consulted.
    assert is_allowed(None, multi_user=False, settings=_settings("")) is True


def test_single_user_mode_ignores_the_allowlist():
    assert is_allowed("anyone", multi_user=False, settings=_settings("someone-else")) is True


# ---------------------------------------------------------------------------
# multi-user mode
# ---------------------------------------------------------------------------


def test_an_empty_allowlist_denies_everyone():
    # Fail closed: an operator who never set the variable has granted nobody.
    assert is_allowed("user-1", multi_user=True, settings=_settings("")) is False


def test_a_listed_user_id_is_allowed():
    assert is_allowed("user-1", multi_user=True, settings=_settings("user-1,user-2")) is True


def test_an_unlisted_user_id_is_denied():
    assert is_allowed("user-9", multi_user=True, settings=_settings("user-1,user-2")) is False


def test_a_listed_email_is_allowed():
    allowed = is_allowed(
        "user-9",
        "tam@example.com",
        multi_user=True,
        settings=_settings("tam@example.com"),
    )
    assert allowed is True


def test_email_matching_ignores_case_and_padding():
    settings = _settings(" TAM@Example.com , user-2 ")
    assert is_allowed(None, "tam@example.com", multi_user=True, settings=settings) is True


def test_an_anonymous_caller_is_denied_under_auth():
    assert is_allowed(None, None, multi_user=True, settings=_settings("user-1")) is False


@pytest.mark.parametrize("raw", ["", "  ", ",", " , "])
def test_blank_allowlist_entries_do_not_admit_blank_identities(raw):
    assert is_allowed("", "", multi_user=True, settings=_settings(raw)) is False


# ---------------------------------------------------------------------------
# environment + message
# ---------------------------------------------------------------------------


def test_auth_enabled_follows_the_server_marker(monkeypatch):
    monkeypatch.delenv("LANGGRAPH_AUTH", raising=False)
    assert auth_enabled() is False
    monkeypatch.setenv("LANGGRAPH_AUTH", '{"path": "x"}')
    assert auth_enabled() is True


def test_mode_defaults_to_the_environment(monkeypatch):
    monkeypatch.delenv("LANGGRAPH_AUTH", raising=False)
    assert is_allowed("anyone", settings=_settings("")) is True


def test_the_refusal_explains_the_rule_and_the_alternative():
    assert "ANALYTICS_AGENT_ALLOWED_USERS" in REFUSAL
    assert "Career Agent" in REFUSAL
