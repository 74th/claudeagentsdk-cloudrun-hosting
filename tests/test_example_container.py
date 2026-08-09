from pathlib import Path


def test_job_dockerfile_uses_non_root_job_entrypoint() -> None:
    dockerfile = Path("example/Dockerfile").read_text()
    assert "USER 10001" in dockerfile
    assert 'ENTRYPOINT ["python", "-m", "example.agent"]' in dockerfile
    assert "PYTHONUNBUFFERED=1" in dockerfile
    assert "FastAPI" not in dockerfile


def test_job_entrypoint_accepts_only_durable_run_environment() -> None:
    source = Path("example/agent/runtime.py").read_text()
    assert "ClaudeAgentConfig" in source
    assert "create_google_cloud_job_composition" in source
    assert "workspace_setup=setup_workspace" in source
    for forbidden in (
        "get_run_for_job",
        "get_session",
        "append_event",
        "commit_terminal",
        "create_workspace_snapshot",
        "extract_snapshot",
        "relocate_claude_transcript",
    ):
        assert forbidden not in source
