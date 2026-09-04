"""Contract tests for the vendored delta-channel WALK (`server.runtime_postgres.checkpoint`).

The vendored checkpointer re-implements upstream's two-pass delta-channel history
reconstruction on top of `langgraph-checkpoint-postgres`'s *private* stage-1 helpers and
mirrors their stage-1 SQL column contract. Those helpers changed shape in 3.1.2
(`hs_i` -> `hb_i` + `inline_i`, two extra positional arguments), which turned every
`GET /threads/{id}/state` into a 500 while the unit suite stayed green. These tests pin
the pieces the vendored walk depends on so the next drift fails here, at `uv run pytest`
time, instead of in a running stack.
"""

import inspect
from collections.abc import Callable
from types import ModuleType
from typing import Any

import pytest
from langgraph.checkpoint.postgres.base import BasePostgresSaver

# Positional layouts the vendored `aget_delta_channel_history` passes.
_INGEST_PARAMS = [
    "stage1_rows",
    "channels",
    "parent_of",
    "ver_by_i_by_cid",
    "hb_by_i_by_cid",
    "inline_by_i_by_cid",
]
_ADVANCE_PARAMS = [
    "target_id",
    "channels",
    "parent_of",
    "ver_by_i_by_cid",
    "hb_by_i_by_cid",
    "inline_by_i_by_cid",
    "chain_by_ch",
    "seed_ver_by_ch",
    "seed_inline_by_ch",
    "walk_cursor_by_ch",
    "seeded",
]


def _param_names(fn: Callable[..., Any]) -> list[str]:
    return list(inspect.signature(fn).parameters)


def test_upstream_ingest_stage1_page_signature_matches_vendored_call():
    assert _param_names(BasePostgresSaver._ingest_stage1_page) == _INGEST_PARAMS  # noqa: SLF001


def test_upstream_try_advance_walks_signature_matches_vendored_call():
    assert _param_names(BasePostgresSaver._try_advance_walks) == _ADVANCE_PARAMS  # noqa: SLF001


@pytest.fixture
def checkpoint_module(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Import the vendored checkpointer; `server.api.config` reads these at import time."""
    monkeypatch.setenv("REDIS_URI", "redis://localhost:6379")
    monkeypatch.setenv("DATABASE_URI", "postgresql://user:pass@localhost:5432/db")
    from server.runtime_postgres import checkpoint

    return checkpoint


@pytest.mark.parametrize("n_channels", [1, 2, 5])
def test_walk_sql_binds_four_params_per_channel(checkpoint_module, n_channels):
    channels = [f"ch{i}" for i in range(n_channels)]
    sql = checkpoint_module._build_delta_walk_sql(channels)  # noqa: SLF001

    # Four per channel, then thread_id, ns, cursor, cursor, page_size.
    assert sql.count("%s") == 4 * n_channels + 5
    for i in range(n_channels):
        assert f"AS ver_{i}" in sql
        assert f"AS hb_{i}" in sql
        assert f"AS inline_{i}" in sql
    assert "hs_" not in sql


def test_walk_sql_probes_blobs_table_by_primary_key(checkpoint_module):
    sql = checkpoint_module._build_delta_walk_sql(["ch"])  # noqa: SLF001

    assert "FROM checkpoint_blobs b0" in sql
    assert "b0.channel = %s" in sql
    assert "b0.version = checkpoint -> 'channel_versions' ->> %s" in sql
    assert "b0.type <> 'empty'" in sql


def test_decode_inline_readings_decodes_fragments_in_place(checkpoint_module):
    fragment = checkpoint_module.Fragment
    page = [
        {"checkpoint_id": "c1", "inline_0": fragment(b"true"), "inline_1": fragment(b"null")},
        {"checkpoint_id": "c2", "inline_0": None, "inline_1": fragment(b'"hello"')},
        {"checkpoint_id": "c3", "inline_0": 3, "inline_1": {"already": "decoded"}},
    ]

    checkpoint_module._decode_inline_readings(page, 2)  # noqa: SLF001

    assert page[0]["inline_0"] is True
    # A JSON ``null`` reads as "no stored value", exactly as upstream's default loader.
    assert page[0]["inline_1"] is None
    assert page[1]["inline_0"] is None
    assert page[1]["inline_1"] == "hello"
    # Cells that are not raw JSONB pass through untouched.
    assert page[2]["inline_0"] == 3
    assert page[2]["inline_1"] == {"already": "decoded"}


def test_decode_inline_readings_blanks_cells_under_encryption(checkpoint_module, monkeypatch):
    from server.api import encryption

    monkeypatch.setattr(encryption, "get_encryption", object)
    page = [{"inline_0": checkpoint_module.Fragment(b"42")}]

    checkpoint_module._decode_inline_readings(page, 1)  # noqa: SLF001

    assert page[0]["inline_0"] is None
