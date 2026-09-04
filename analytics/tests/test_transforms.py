"""Unit tests for the extraction transforms — the privacy contract lives here."""

from nextrole_analytics.dlt_sources.transforms import (
    extract_model_overrides,
    hash_email,
    shape_checkpoint_meta,
    shape_run,
    shape_session,
    shape_thread,
    shape_user,
    truncate_ip,
)


def test_hash_email_normalizes_and_keeps_domain():
    digest, domain = hash_email("  Alex.Rivera@Example.COM ")
    same_digest, _ = hash_email("alex.rivera@example.com")
    assert digest == same_digest
    assert domain == "example.com"
    assert digest is not None
    assert "alex" not in digest


def test_hash_email_handles_missing_and_malformed():
    assert hash_email(None) == (None, None)
    assert hash_email("") == (None, None)
    digest, domain = hash_email("not-an-email")
    assert digest is not None
    assert domain is None


def test_truncate_ip_v4_to_slash_24():
    assert truncate_ip("203.0.113.87") == "203.0.113.0/24"


def test_truncate_ip_drops_v6_and_garbage():
    assert truncate_ip("2001:db8::1") is None
    assert truncate_ip("999.1.1.1") is None
    assert truncate_ip("localhost") is None
    assert truncate_ip(None) is None


def test_extract_model_overrides_whitelists_scalars_only():
    configurable = {
        "main_agent_model": "gpt-5.2",
        "subagent_model": {"nested": "payload"},
        "user_prompt": "SECRET CV TEXT",
        "some_model_config": "ignored-key",
    }
    overrides = extract_model_overrides(configurable)
    assert overrides == {"main_agent_model": "gpt-5.2", "subagent_model": None}


def test_shape_run_drops_metadata_and_kwargs_wholesale():
    row = {
        "run_id": "r1",
        "status": "success",
        "metadata": {"owner": "user-1", "arbitrary": "client data"},
        "kwargs": {
            "input": {"messages": [{"content": "SECRET CV TEXT"}]},
            "config": {"configurable": {"main_agent_model": "claude-sonnet-5"}},
        },
    }
    shaped = shape_run(row)
    assert shaped["owner"] == "user-1"
    assert shaped["main_model_override"] == "claude-sonnet-5"
    assert shaped["subagent_model_override"] is None
    assert "metadata" not in shaped
    assert "kwargs" not in shaped
    assert "SECRET" not in str(shaped)


def test_shape_thread_ships_counts_never_bodies():
    row = {
        "thread_id": "t1",
        "status": "idle",
        "metadata": {"owner": "user-1", "graph_id": "career_agent", "assistant_id": "a1"},
        "values": {
            "messages": [{"content": "SECRET"}, {"content": "ALSO SECRET"}],
            "files": {"/upload/cv.pdf": {"content": "SECRET BODY", "encoding": "base64"}},
            "todos": [{"content": "step", "status": "pending"}],
        },
        "interrupts": {"task-1": [{"value": "approve?"}]},
    }
    shaped = shape_thread(row)
    assert shaped["owner"] == "user-1"
    assert shaped["graph_id"] == "career_agent"
    assert shaped["message_count"] == 2
    assert shaped["file_count"] == 1
    assert shaped["todo_count"] == 1
    assert shaped["has_interrupt"] is True
    assert "values" not in shaped
    assert "interrupts" not in shaped
    assert "metadata" not in shaped
    assert "SECRET" not in str(shaped)


def test_shape_thread_tolerates_null_values_column():
    shaped = shape_thread({"thread_id": "t1", "metadata": None, "values": None, "interrupts": None})
    assert shaped["message_count"] == 0
    assert shaped["file_count"] == 0
    assert shaped["has_interrupt"] is False
    assert shaped["owner"] is None


def test_shape_checkpoint_meta_whitelists_keys():
    row = {
        "thread_id": "t1",
        "checkpoint_ns": "",
        "checkpoint_id": "c1",
        "run_id": "r1",
        "ts": "2026-08-29T05:00:00+00:00",
        "metadata": {
            "source": "loop",
            "step": 4,
            "graph_id": "career_agent",
            "assistant_id": "a1",
            "owner": "user-1",
            "user_id": "user-1",
            "writes": {"messages": "SECRET STATE"},
        },
    }
    shaped = shape_checkpoint_meta(row)
    assert shaped["source"] == "loop"
    assert shaped["step"] == 4
    assert shaped["owner"] == "user-1"
    assert "metadata" not in shaped
    assert "SECRET" not in str(shaped)


def test_shape_user_replaces_email_with_hash_and_domain():
    row = {
        "id": "u1",
        "name": "Alex Rivera",
        "email": "alex.rivera@example.com",
        "emailVerified": True,
        "createdAt": "2026-08-01T00:00:00+00:00",
        "updatedAt": "2026-08-01T00:00:00+00:00",
    }
    shaped = shape_user(row)
    assert "email" not in shaped
    assert shaped["email_domain"] == "example.com"
    assert shaped["email_hash"] is not None
    assert "alex.rivera@" not in str(shaped)


def test_shape_session_coarsens_network_identifiers():
    row = {
        "id": "s1",
        "userId": "u1",
        "ipAddress": "203.0.113.87",
        "userAgent": "Mozilla/5.0 " + "x" * 500,
        "createdAt": "2026-08-01T00:00:00+00:00",
        "updatedAt": "2026-08-01T00:00:00+00:00",
    }
    shaped = shape_session(row)
    assert "ipAddress" not in shaped
    assert "userAgent" not in shaped
    assert shaped["ip_prefix"] == "203.0.113.0/24"
    assert len(shaped["user_agent"]) == 256
