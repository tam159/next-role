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
#: Also the default ``agent_root`` for object keys — a second agent passes its
#: own root (see ``object_storage.AREA_ROOTS``) so its artifacts land under
#: ``users/<scope>/<its root>/`` instead.
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


def current_user() -> object | None:
    """Return the authenticated caller's user object for the active run, or ``None``.

    The server's custom-auth layer stores whatever :mod:`backend.agents.auth`
    returned (wrapped so attributes resolve), which carries ``identity`` plus
    any extra claims the token provided — ``email`` among them. Use
    :func:`current_identity` when only the id is needed.
    """
    try:
        config = get_config()
    except RuntimeError:
        return None
    configurable = config.get("configurable") or {}
    return configurable.get("langgraph_auth_user")


def current_thread_id() -> str | None:
    """Return the active run's thread id, or ``None`` outside a runnable context.

    Used to key per-thread artifacts (chart files, scratch dirs) so two
    conversations never overwrite each other's outputs.
    """
    try:
        config = get_config()
    except RuntimeError:
        return None
    configurable = config.get("configurable") or {}
    thread_id = configurable.get("thread_id")
    return str(thread_id) if thread_id else None


def kv_namespace(
    area: str,
    identity: str | None = None,
    *,
    agent_root: str = KV_ROOT,
) -> tuple[str, ...]:
    """Namespace tuple for a KV-store ``area``, scoped to ``identity`` if any.

    ``identity`` defaults to :func:`current_identity`; a user segment is
    prepended only when an identity is present, so single-user namespaces stay
    ``(agent_root, area)``. ``agent_root`` defaults to the career agent's root
    so existing namespaces are unchanged.
    """
    if identity is None:
        identity = current_identity()
    if identity:
        return (identity, agent_root, area)
    return (agent_root, area)


def object_scope(identity: str | None = None, *, agent_root: str = KV_ROOT) -> str:
    """Object-key scope prefix (``users/<identity>/<agent_root>``).

    ``identity`` defaults to :func:`current_identity`; absence maps to
    :data:`DEFAULT_OBJECT_SCOPE`, preserving the historical single-user layout.
    ``agent_root`` defaults to the career agent's root, so existing keys are
    byte-identical; a second agent's areas pass their own root.
    """
    if identity is None:
        identity = current_identity()
    return f"users/{identity or DEFAULT_OBJECT_SCOPE}/{agent_root}"
