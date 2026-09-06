"""Which JWT claims reach the rest of the backend.

`identity` is the only claim that drives authorization — every namespace, key
and ownership filter is built from it. The others are descriptive, and this
pins which ones survive so a feature that depends on one (the analytics
allowlist reads `email`) fails here rather than in production.
"""

import os

import pytest

# `auth.py` reads AUTH_JWKS_URL at import and raises without it (a deliberate
# fail-fast for the misconfigured deployment), so it must be set before the
# import below rather than in a fixture.
os.environ.setdefault("AUTH_JWKS_URL", "http://frontend:3000/api/auth/jwks")

from backend.agents import auth


class _FakeSigningKey:
    key = "unused"


@pytest.fixture
def authenticate(monkeypatch):
    """The real handler with signature verification stubbed out."""
    captured: dict = {}

    def fake_decode(token, key, **kwargs):
        return captured["claims"]

    monkeypatch.setattr(auth.jwt, "decode", fake_decode)
    monkeypatch.setattr(
        auth._jwk_client,  # noqa: SLF001 — stubbing the network dependency
        "get_signing_key_from_jwt",
        lambda _token: _FakeSigningKey(),
    )

    async def run(claims: dict):
        captured["claims"] = claims
        return await auth.authenticate(authorization="Bearer token")

    return run


async def test_identity_comes_from_the_subject_claim(authenticate):
    user = await authenticate({"sub": "user-1"})

    assert user["identity"] == "user-1"


async def test_display_name_and_email_ride_along(authenticate):
    user = await authenticate({"sub": "user-1", "name": "Tam", "email": "tam@example.com"})

    assert user["display_name"] == "Tam"
    assert user["email"] == "tam@example.com"


async def test_absent_optional_claims_are_simply_omitted(authenticate):
    user = await authenticate({"sub": "user-1"})

    assert "display_name" not in user
    assert "email" not in user


async def test_non_string_claims_are_ignored(authenticate):
    user = await authenticate({"sub": "user-1", "name": 42, "email": {"a": 1}})

    assert "display_name" not in user
    assert "email" not in user


async def test_a_missing_bearer_token_is_rejected():
    with pytest.raises(auth.Auth.exceptions.HTTPException):
        await auth.authenticate(authorization=None)


async def test_a_non_bearer_scheme_is_rejected():
    with pytest.raises(auth.Auth.exceptions.HTTPException):
        await auth.authenticate(authorization="Basic abc")
