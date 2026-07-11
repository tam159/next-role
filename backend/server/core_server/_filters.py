"""Translate custom-auth ``AuthFilter`` protos into parameterized SQL predicates.

The HTTP ops layer (server/api/grpc/ops) runs the deployment's custom-auth
handlers and ships the resulting ownership filters on request protos
(``request.filters``, ``request.thread_filters``, ``request.assistant_filters``).
The native servicers translate them here into WHERE predicates so that
authorization is enforced at the data plane — an unowned row is
indistinguishable from a missing one (NOT_FOUND, never a 403 existence
oracle).

With no filters (single-user / noop auth) every helper returns ``""`` and
queries stay byte-for-byte what they were before multi-user support.

Safety: filter values are always bound as psycopg named parameters (wrapped
in ``Jsonb``) — never interpolated into SQL. Metadata matching uses JSONB
containment (``@>``), which the existing GIN ``jsonb_path_ops`` indexes on
``thread.metadata`` / ``assistant.metadata`` satisfy. ``column`` /
``thread_id_expr`` arguments are trusted literals supplied by servicer code,
never request data.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import orjson
from psycopg.types.json import Jsonb

if TYPE_CHECKING:
    from collections.abc import Iterable

    from server.grpc_common.proto import core_api_pb2 as pb


def filters_clause(
    filters: Iterable[pb.AuthFilter],
    params: dict[str, Any],
    *,
    column: str = "metadata",
    param_prefix: str = "_af",
) -> str:
    """Build one SQL predicate for a request's AuthFilter list.

    Args:
        filters: repeated ``pb.AuthFilter`` field from a request proto.
        params: the query's named-parameter dict; bindings are added in place
            under collision-free ``{param_prefix}N`` names.
        column: trusted SQL text for the JSONB column the filters target
            (optionally alias-qualified, e.g. ``"thread.metadata"``).
        param_prefix: prefix for generated parameter names.

    Returns:
        ``""`` when there is nothing to enforce, else a parenthesized
        predicate ANDing all top-level filters (upstream semantics: the
        filter list is a conjunction).
    """
    if not filters:
        return ""
    state = _BindState(params, param_prefix)
    preds = [p for f in filters if (p := _predicate(f, state, column))]
    if not preds:
        return ""
    return "(" + " AND ".join(preds) + ")"


def thread_owner_clause(
    filters: Iterable[pb.AuthFilter],
    params: dict[str, Any],
    *,
    thread_id_expr: str,
    param_prefix: str = "_af",
) -> str:
    """Ownership predicate for run rows via their parent thread.

    Runs carry no owner column of their own — ownership is the parent
    thread's metadata — so run RPCs wrap the thread filter in an EXISTS
    subquery. ``thread_id_expr`` is trusted SQL text (e.g. ``"run.thread_id"``).
    """
    inner = filters_clause(
        filters,
        params,
        column="_af_thread.metadata",
        param_prefix=param_prefix,
    )
    if not inner:
        return ""
    return (
        "EXISTS (SELECT 1 FROM thread AS _af_thread "
        f"WHERE _af_thread.thread_id = {thread_id_expr} AND {inner})"
    )


class _BindState:
    """Allocates collision-free named parameters into an existing dict."""

    def __init__(self, params: dict[str, Any], prefix: str) -> None:
        self.params = params
        self.prefix = prefix
        self.n = 0

    def bind(self, value: Any) -> str:
        while (name := f"{self.prefix}{self.n}") in self.params:
            self.n += 1
        self.params[name] = value
        self.n += 1
        return name


def _predicate(f: pb.AuthFilter, state: _BindState, column: str) -> str:
    kind = f.WhichOneof("filter")
    if kind == "eq":
        # match is JSON text (see ops._serialize_filter_value); containment on
        # a single key-value pair is equality for scalar values.
        value = orjson.loads(f.eq.match)
        name = state.bind(Jsonb({f.eq.key: value}))
        return f"{column} @> %({name})s"
    if kind == "contains":
        preds = []
        for match in f.contains.matches:
            value = orjson.loads(match)
            name = state.bind(Jsonb({f.contains.key: [value]}))
            preds.append(f"{column} @> %({name})s")
        return _join(preds, " OR ")
    if kind == "and_filter":
        preds = [p for sub in f.and_filter.filters if (p := _predicate(sub, state, column))]
        return _join(preds, " AND ")
    if kind == "or_filter":
        preds = [p for sub in f.or_filter.filters if (p := _predicate(sub, state, column))]
        return _join(preds, " OR ")
    return ""


def _join(preds: list[str], sep: str) -> str:
    if not preds:
        return ""
    if len(preds) == 1:
        return preds[0]
    return "(" + sep.join(preds) + ")"
