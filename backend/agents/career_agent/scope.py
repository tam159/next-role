"""Per-user scoping of the agent's persisted artifacts.

Resolves the caller's identity at call time from the LangGraph run config
(``configurable.langgraph_auth_user`` / ``langgraph_auth_user_id``, injected by
the server's custom-auth layer and restored by the worker for each run). With
no custom auth configured the identity is ``None`` and both storage tiers fall
back to their single-user layout — byte-for-byte what they were before
multi-user support.

The two tiers scope differently, to keep zero-login data exactly where it is:

* **KV store** (Postgres, DeepAgents ``StoreBackend``) had namespaces
  ``("career_agent", <area>)`` with no user segment, so a real identity is
  *prepended* and absent identity yields the original 2-tuple.
* **Object store** keys already carried a ``users/default/`` segment, so the
  identity simply *replaces* ``default``.
"""

from __future__ import annotations

from langgraph.config import get_config

#: Object-store scope for single-user / unauthenticated use. Matches the
#: historical ``users/default/`` key segment so existing artifacts stay put.
DEFAULT_OBJECT_SCOPE = "default"

#: Root segment shared by every KV-store namespace (after any user segment).
KV_ROOT = "career_agent"


def current_identity() -> str | None:
    """Return the authenticated caller's identity for the active run, or ``None``.

    ``None`` in single-user mode (no custom auth) and whenever called outside a
    runnable context (e.g. a unit test invoking a backend directly).
    """
    try:
        config = get_config()
    except RuntimeError:
        return None
    configurable = config.get("configurable") or {}
    user = configurable.get("langgraph_auth_user")
    identity = getattr(user, "identity", None) if user is not None else None
    if not identity:
        identity = configurable.get("langgraph_auth_user_id")
    # Noop auth yields an empty-string identity — treat it as unscoped.
    return identity or None


def kv_namespace(area: str, identity: str | None = None) -> tuple[str, ...]:
    """Namespace tuple for a KV-store ``area``, scoped to ``identity`` if any.

    ``identity`` defaults to :func:`current_identity`; a user segment is
    prepended only when an identity is present, so single-user namespaces stay
    ``(KV_ROOT, area)``.
    """
    if identity is None:
        identity = current_identity()
    if identity:
        return (identity, KV_ROOT, area)
    return (KV_ROOT, area)


def object_scope(identity: str | None = None) -> str:
    """Object-key scope prefix (``users/<identity>/career_agent``).

    ``identity`` defaults to :func:`current_identity`; absence maps to
    :data:`DEFAULT_OBJECT_SCOPE`, preserving the historical single-user layout.
    """
    if identity is None:
        identity = current_identity()
    return f"users/{identity or DEFAULT_OBJECT_SCOPE}/{KV_ROOT}"
