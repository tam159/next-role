"""Unit tests for the AuthFilter→SQL translation (`server.core_server._filters`).

These protos are what the ops layer ships after running the custom-auth
handlers; the servicers must turn them into parameterized predicates without
ever interpolating a value into SQL.
"""

from psycopg.types.json import Jsonb

from server.core_server._filters import filters_clause, thread_owner_clause
from server.grpc_common.proto import core_api_pb2 as pb


def _eq(key: str, match_json: str) -> pb.AuthFilter:
    f = pb.AuthFilter()
    f.eq.CopyFrom(pb.EqAuthFilter(key=key, match=match_json))
    return f


def _contains(key: str, matches: list[str]) -> pb.AuthFilter:
    f = pb.AuthFilter()
    f.contains.CopyFrom(pb.ContainsAuthFilter(key=key, matches=matches))
    return f


def _obj(param: Jsonb) -> dict:
    return param.obj


def test_no_filters_is_a_noop() -> None:
    params: dict = {}
    assert filters_clause([], params) == ""
    assert params == {}


def test_single_eq_binds_jsonb_containment() -> None:
    params: dict = {}
    clause = filters_clause([_eq("owner", '"user-1"')], params)
    assert clause == "(metadata @> %(_af0)s)"
    assert _obj(params["_af0"]) == {"owner": "user-1"}


def test_hostile_key_and_value_never_reach_the_sql_text() -> None:
    hostile_key = "owner') OR 1=1; DROP TABLE thread; --"
    hostile_value = '"\'; DELETE FROM run; --"'
    params: dict = {}
    clause = filters_clause([_eq(hostile_key, hostile_value)], params)
    assert clause == "(metadata @> %(_af0)s)"
    assert "DROP" not in clause
    assert "DELETE" not in clause
    assert _obj(params["_af0"]) == {hostile_key: "'; DELETE FROM run; --"}


def test_contains_ors_each_match_as_array_containment() -> None:
    params: dict = {}
    clause = filters_clause([_contains("tags", ['"a"', '"b"'])], params)
    assert clause == "((metadata @> %(_af0)s OR metadata @> %(_af1)s))"
    assert _obj(params["_af0"]) == {"tags": ["a"]}
    assert _obj(params["_af1"]) == {"tags": ["b"]}


def test_top_level_filters_are_anded() -> None:
    params: dict = {}
    clause = filters_clause([_eq("owner", '"u1"'), _eq("org", '"o1"')], params)
    assert clause == "(metadata @> %(_af0)s AND metadata @> %(_af1)s)"


def test_or_and_nesting() -> None:
    or_filter = pb.AuthFilter()
    and_branch = pb.AuthFilter()
    and_branch.and_filter.CopyFrom(
        pb.AndAuthFilter(filters=[_eq("owner", '"u1"'), _eq("org", '"o1"')]),
    )
    or_filter.or_filter.CopyFrom(pb.OrAuthFilter(filters=[and_branch, _eq("owner", '"u2"')]))
    params: dict = {}
    clause = filters_clause([or_filter], params)
    assert clause == ("(((metadata @> %(_af0)s AND metadata @> %(_af1)s) OR metadata @> %(_af2)s))")


def test_param_names_never_collide_with_existing_bindings() -> None:
    params: dict = {"_af0": "taken", "tid": "x"}
    clause = filters_clause([_eq("owner", '"u1"')], params)
    assert clause == "(metadata @> %(_af1)s)"
    assert params["_af0"] == "taken"


def test_column_qualification_is_used_verbatim() -> None:
    params: dict = {}
    clause = filters_clause([_eq("owner", '"u1"')], params, column="thread.metadata")
    assert clause == "(thread.metadata @> %(_af0)s)"


def test_thread_owner_clause_wraps_in_exists_subquery() -> None:
    params: dict = {}
    clause = thread_owner_clause([_eq("owner", '"u1"')], params, thread_id_expr="run.thread_id")
    assert clause == (
        "EXISTS (SELECT 1 FROM thread AS _af_thread "
        "WHERE _af_thread.thread_id = run.thread_id AND (_af_thread.metadata @> %(_af0)s))"
    )
    assert _obj(params["_af0"]) == {"owner": "u1"}


def test_thread_owner_clause_empty_without_filters() -> None:
    params: dict = {}
    assert thread_owner_clause([], params, thread_id_expr="run.thread_id") == ""
    assert params == {}


def test_non_scalar_eq_value_binds_as_object() -> None:
    params: dict = {}
    clause = filters_clause([_eq("owner", '{"team": "a"}')], params)
    assert clause == "(metadata @> %(_af0)s)"
    assert _obj(params["_af0"]) == {"owner": {"team": "a"}}
