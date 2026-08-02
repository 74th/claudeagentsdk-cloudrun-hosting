from typing import get_type_hints

from cas_hosting_adapter.protocols import ChatStore, ExecutionBackend, WorkspaceStore


def test_provider_ports_expose_domain_models_only() -> None:
    for port in (ExecutionBackend, ChatStore, WorkspaceStore):
        assert "google" not in str(get_type_hints(port, include_extras=True)).lower()
