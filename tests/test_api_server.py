from fastapi.testclient import TestClient

from cas_hosting_adapter.api_server import create_app
from cas_hosting_adapter.models import HostingSettings


def app() -> TestClient:
    async def agent(message: str) -> str:
        return f"echo: {message}"

    settings = HostingSettings(
        project="project", location="us-central1", agent_engine="engine", bucket_name="bucket"
    )
    return TestClient(create_app(agent, settings))


def test_normal_runtime_contract_accepts_json_string() -> None:
    response = app().post(
        "/api/reasoning_engine",
        json='{"class_method":"query","input":{"user_id":"user","message":"Hello"}}',
    )
    assert response.status_code == 200
    assert response.json()["output"] == "echo: Hello"
    assert response.json()["run_id"]


def test_stream_runtime_contract_returns_ndjson() -> None:
    response = app().post(
        "/api/stream_reasoning_engine",
        json='{"class_method":"stream_query","input":{"user_id":"user","message":"Hello"}}',
    )
    assert response.status_code == 200
    assert response.text.endswith("\n")
    assert '"output": "echo: Hello"' in response.text


def test_message_limit_is_rejected() -> None:
    response = app().post(
        "/api/reasoning_engine",
        json={"class_method": "query", "input": {"user_id": "user", "message": "x" * 1001}},
    )
    assert response.status_code == 400
