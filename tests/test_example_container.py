from pathlib import Path


def test_job_dockerfile_uses_non_root_job_entrypoint() -> None:
    dockerfile = Path("example/Dockerfile").read_text()
    assert "USER 10001" in dockerfile
    assert 'ENTRYPOINT ["python", "-m", "example.agent"]' in dockerfile
    assert "PYTHONUNBUFFERED=1" in dockerfile
    assert "FastAPI" not in dockerfile


def test_job_entrypoint_accepts_only_durable_run_environment() -> None:
    source = Path("example/agent/runtime.py").read_text()
    assert "JobInvocation.from_environment" in source
    assert "request_directories" in source
    assert "WORKSPACE_BUCKET" in source
    assert "commit_success" in source
    assert "relocate_claude_transcript" in source
