from datetime import datetime, timedelta

from yuxi.storage.postgres.models_business import AgentRun, build_agent_run_timing


def test_agent_run_timing_derives_stage_latencies_from_authoritative_timestamps():
    created_at = datetime(2026, 9, 4, 8, 0, 0)
    timing = build_agent_run_timing(
        created_at=created_at,
        started_at=created_at + timedelta(milliseconds=200),
        prepared_at=created_at + timedelta(milliseconds=1000),
        first_output_at=created_at + timedelta(milliseconds=7500),
        finished_at=created_at + timedelta(milliseconds=43670),
        first_model_request_at=created_at + timedelta(milliseconds=1500),
    )

    assert timing == {
        "created_at": "2026-09-04T08:00:00Z",
        "started_at": "2026-09-04T08:00:00.200000Z",
        "prepared_at": "2026-09-04T08:00:01Z",
        "first_model_request_at": "2026-09-04T08:00:01.500000Z",
        "first_output_at": "2026-09-04T08:00:07.500000Z",
        "finished_at": "2026-09-04T08:00:43.670000Z",
        "dispatch_latency_ms": 200,
        "preparation_latency_ms": 800,
        "first_model_request_latency_ms": 1500,
        "model_first_output_latency_ms": 6500,
        "first_output_latency_ms": 7500,
        "total_latency_ms": 43670,
    }


def test_agent_run_timing_keeps_missing_and_invalid_intervals_unknown():
    created_at = datetime(2026, 9, 4, 8, 0, 1)
    timing = build_agent_run_timing(
        created_at=created_at,
        started_at=created_at - timedelta(seconds=1),
        prepared_at=None,
        first_output_at=created_at + timedelta(seconds=1),
        finished_at=None,
    )

    assert timing["dispatch_latency_ms"] is None
    assert timing["preparation_latency_ms"] is None
    assert timing["model_first_output_latency_ms"] is None
    assert timing["first_output_latency_ms"] == 1000
    assert timing["first_model_request_latency_ms"] is None
    assert timing["total_latency_ms"] is None


def test_agent_run_timing_projects_only_supported_sandbox_metrics():
    timing = build_agent_run_timing(
        created_at=None,
        started_at=None,
        prepared_at=None,
        first_output_at=None,
        finished_at=None,
        sandbox_timing={
            "sandbox_discover_ms": 1.234,
            "sandbox_execute_request_ms": 9,
            "sandbox_command_ms": 8,
            "unknown_ms": 7,
            "sandbox_release_ms": True,
            "sandbox_delete_network_ms": -1,
        },
    )

    assert timing["sandbox_discover_ms"] == 1.23
    assert timing["sandbox_execute_request_ms"] == 9.0
    assert "sandbox_command_ms" not in timing
    assert "unknown_ms" not in timing
    assert "sandbox_release_ms" not in timing
    assert "sandbox_delete_network_ms" not in timing


def test_agent_run_dict_uses_the_shared_timing_projection():
    created_at = datetime(2026, 9, 4, 8, 0, 0)
    run = AgentRun(
        id="run-1",
        conversation_thread_id="thread-1",
        runtime_scope_id="thread-1",
        agent_slug="main",
        uid="user-1",
        request_id="request-1",
        input_payload={},
        created_at=created_at,
        started_at=created_at + timedelta(seconds=1),
        prepared_at=created_at + timedelta(seconds=2),
        first_output_at=created_at + timedelta(seconds=4),
        finished_at=created_at + timedelta(seconds=8),
        first_model_request_at=created_at + timedelta(seconds=3),
        sandbox_timing={"sandbox_wait_ready_ms": 2500.5},
    )

    payload = run.to_dict()

    assert payload["prepared_at"] == "2026-09-04T08:00:02Z"
    assert payload["first_model_request_at"] == "2026-09-04T08:00:03Z"
    assert payload["first_output_at"] == "2026-09-04T08:00:04Z"
    assert payload["timing"]["preparation_latency_ms"] == 1000
    assert payload["timing"]["first_model_request_latency_ms"] == 3000
    assert payload["timing"]["first_output_latency_ms"] == 4000
    assert payload["timing"]["sandbox_wait_ready_ms"] == 2500.5
    assert payload["sandbox_timing"] == {"sandbox_wait_ready_ms": 2500.5}
