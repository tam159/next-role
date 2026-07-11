"""Custom authentication + authorization for multi-user mode.

Loaded by the agent server when ``LANGGRAPH_AUTH`` points here
(``{"path": "/deps/next-role/backend/agents/auth.py:auth", "disable_studio_auth": true}``).
With ``LANGGRAPH_AUTH`` unset this module is never imported and the server
runs in its zero-login single-user mode.

Authentication: every request must carry ``Authorization: Bearer <JWT>``
minted by the frontend's Better Auth JWT plugin (``/api/auth/token``). The
token is verified against the frontend's JWKS (``AUTH_JWKS_URL``) with the
algorithm pinned to EdDSA — never trusted from the token header — plus
issuer/audience checks when configured. The JWT ``sub`` claim (the Better
Auth user id) becomes the identity that owns threads, files, and memory.

Authorization ("single-owner resources"): the most specific ``@auth.on``
handler runs per request. Threads (which also cover runs — run events
dispatch under the ``threads`` resource) and crons stamp
``metadata.owner = identity`` on create and return an ``{"owner": identity}``
filter that the core-server servicers enforce in SQL. Assistants are shared:
readable by every authenticated user, mutable by none. Store namespaces are
rewritten so their first segment is always the caller's identity. Anything
without a specific handler falls into the global default-deny.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import jwt
from jwt import PyJWKClient
from langgraph_sdk import Auth

# Required when this module is active; fail fast at import with a clear name.
_JWKS_URL = os.environ["AUTH_JWKS_URL"]
_ISSUER = os.environ.get("AUTH_JWT_ISSUER") or None
_AUDIENCE = os.environ.get("AUTH_JWT_AUDIENCE") or None

# Better Auth's JWT plugin signs with EdDSA (Ed25519) by default. Pin it —
# accepting the token header's alg would let a caller downgrade verification.
_ALGORITHMS = ["EdDSA"]

# PyJWKClient caches fetched keys (lifespan below) so the thread hop for its
# blocking HTTP fetch is rare after startup.
_jwk_client = PyJWKClient(_JWKS_URL, cache_keys=True, lifespan=300)

auth = Auth()


@auth.authenticate
async def authenticate(authorization: str | None) -> Auth.types.MinimalUserDict:
    """Verify the Better Auth bearer JWT and resolve the caller's identity.

    Uses the ``authorization`` parameter (not ``request``) so the same
    handler serves HTTP and WebSocket scopes.
    """
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise Auth.exceptions.HTTPException(
            status_code=401,
            detail="Missing bearer token",
        )
    try:
        signing_key = await asyncio.to_thread(_jwk_client.get_signing_key_from_jwt, token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=_ALGORITHMS,
            issuer=_ISSUER,
            audience=_AUDIENCE,
            options={
                "require": ["exp", "sub"],
                "verify_iss": _ISSUER is not None,
                "verify_aud": _AUDIENCE is not None,
            },
        )
    except jwt.PyJWTError as e:
        raise Auth.exceptions.HTTPException(
            status_code=401,
            detail=f"Invalid token: {e}",
        ) from None
    # Only `identity` drives authorization/scoping; carry a display name when
    # the token provides one (MinimalUserDict has no email field).
    user: Auth.types.MinimalUserDict = {"identity": claims["sub"]}
    if isinstance(name := claims.get("name"), str):
        user["display_name"] = name
    return user


def _stamp_owner(value: dict[str, Any], identity: str) -> None:
    """Write ``owner`` into the resource's metadata (persisted on create).

    Mutating ``value["metadata"]`` is the contract: for run creation the ops
    layer serializes this same dict into the run row and into the metadata of
    an implicitly-created thread, which is what the SQL filters match on.
    """
    if isinstance(value, dict):
        metadata = value.setdefault("metadata", None)
        if metadata is None:
            value["metadata"] = {"owner": identity}
        elif isinstance(metadata, dict):
            metadata["owner"] = identity


@auth.on.threads
async def authorize_threads(
    ctx: Auth.types.AuthContext,
    value: dict[str, Any],
) -> dict[str, Any]:
    """Threads (and runs — same resource) are private to their owner."""
    _stamp_owner(value, ctx.user.identity)
    return {"owner": ctx.user.identity}


@auth.on.crons
async def authorize_crons(
    ctx: Auth.types.AuthContext,
    value: dict[str, Any],
) -> dict[str, Any]:
    """Crons are private to their owner (same metadata convention)."""
    _stamp_owner(value, ctx.user.identity)
    return {"owner": ctx.user.identity}


@auth.on.assistants
async def authorize_assistants(
    ctx: Auth.types.AuthContext,
    value: dict[str, Any],  # noqa: ARG001  (required by the @auth.on keyword-call contract)
) -> bool | None:
    """Assistants are shared system config: read for everyone, write for no one.

    The career_agent assistant is registered by the deployment itself
    (LANGSERVE_GRAPHS), not created by users.
    """
    if ctx.action in ("create", "update", "delete"):
        return False
    return None


@auth.on.store
async def authorize_store(
    ctx: Auth.types.AuthContext,
    value: dict[str, Any],
) -> None:
    """Scope every store namespace to the caller.

    Rewrite pattern: the caller keeps using logical namespaces (e.g.
    ``("career_agent", "memory")``) and the identity is prepended server-side,
    landing on the same physical rows the agent's per-user StoreBackend
    namespaces address. A namespace already starting with the identity is
    left as-is. A foreign prefix simply becomes a scoped (empty) subtree —
    cross-user reads are impossible by construction.
    """
    identity = ctx.user.identity
    namespace = tuple(value.get("namespace") or ())
    if not namespace or namespace[0] != identity:
        value["namespace"] = (identity, *namespace)


@auth.on
async def deny_unhandled(
    ctx: Auth.types.AuthContext,  # noqa: ARG001  (required by the @auth.on keyword-call contract)
    value: dict[str, Any],  # noqa: ARG001
) -> bool:
    """Fail closed: any resource/action without a specific handler is denied."""
    return False
