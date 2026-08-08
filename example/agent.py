"""Cloud Run Job entrypoint for one persisted RUN_ID."""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
from importlib.metadata import version
from pathlib import Path

from cas_hosting_adapter.agent_adapter import ClaudeAgentAdapter
from cas_hosting_adapter.firestore_chat_store import FirestoreChatStore
from cas_hosting_adapter.google_adapters import GCSWorkspaceStore
from cas_hosting_adapter.job_runner import JobInvocation, JobRunner
from cas_hosting_adapter.models import RunState
from cas_hosting_adapter.workspace_store import (
    StoragePaths,
    create_workspace_snapshot,
    extract_snapshot,
    request_directories,
)

LOGGER = logging.getLogger(__name__)


def relocate_claude_transcript(transcript_dir: Path, workspace: Path) -> None:
    """Map a restored Claude transcript to this Job's new temporary workspace."""
    projects = transcript_dir / ".claude" / "projects"
    if not projects.exists():
        return
    workspace_key = str(workspace.resolve()).replace("/", "-")
    destination = projects / workspace_key
    destination.mkdir(parents=True, exist_ok=True)
    for transcript in projects.glob("*/*.jsonl"):
        target = destination / transcript.name
        if transcript != target:
            shutil.copy2(transcript, target)


async def run() -> int:
    invocation = JobInvocation.from_environment()
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    from google.cloud.firestore import Client
    from google.cloud.storage import Client as StorageClient  # type: ignore[import-untyped]

    project = os.environ["GOOGLE_CLOUD_PROJECT"]
    database = os.environ["FIRESTORE_DATABASE"]
    model = os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5@20251001")
    LOGGER.info(
        "job.start run_id=%s execution=%s project=%s database=%s model=%s",
        invocation.run_id,
        invocation.execution_identity,
        project,
        database,
        model,
    )
    store = FirestoreChatStore(Client(project=project, database=database))
    runner = JobRunner(store)
    runner.install_sigterm_handler()
    prompt = runner.prompt_for_run(invocation.run_id)
    LOGGER.info("job.prompt.loaded run_id=%s", invocation.run_id)
    try:
        with request_directories() as directories:
            LOGGER.info("job.workspace.ready run_id=%s", invocation.run_id)
            current_run = store.get_run_for_job(invocation.run_id)
            previous = store.get_session(current_run.user_id, current_run.session_id)
            bucket_name = os.environ["WORKSPACE_BUCKET"]
            workspace_store = GCSWorkspaceStore(
                StorageClient(project=project).bucket(bucket_name)
            )
            resume = None
            if previous.snapshot is not None and previous.claude_session_id is not None:
                archive = directories.root / "resume.tar.gz"
                archive.write_bytes(workspace_store.get(previous.snapshot))
                try:
                    extract_snapshot(archive, directories, max_bytes=100 * 1024 * 1024)
                finally:
                    archive.unlink(missing_ok=True)
                relocate_claude_transcript(directories.claude_session, directories.workspace)
                resume = previous.claude_session_id
                LOGGER.info("job.session.resumed run_id=%s", invocation.run_id)
            agent = ClaudeAgentAdapter(model=model)
            state = await runner.persist_events(
                invocation,
                agent.events(
                    prompt=prompt,
                    workspace=directories.workspace,
                    transcript_dir=directories.claude_session,
                    resume=resume,
                ),
            )
            if state.value == "running":
                run = store.get_run_for_job(invocation.run_id)
                paths = StoragePaths.for_session(
                    user_id=run.user_id,
                    session_id=run.session_id,
                    schema_version=run.schema_version,
                    sdk_version=version("claude-agent-sdk"),
                )
                snapshot, _manifest = create_workspace_snapshot(
                    workspace_store,
                    object_key=paths.snapshot_path(invocation.run_id),
                    source=directories,
                    run_id=invocation.run_id,
                    sdk_version=version("claude-agent-sdk"),
                    max_bytes=100 * 1024 * 1024,
                )
                state = runner.commit_success(
                    invocation,
                    result=runner.result_for_run(invocation.run_id),
                    snapshot=snapshot,
                    claude_session_id=runner.claude_session_id,
                ).state
            else:
                state = runner.commit_unsuccessful(
                    invocation, state, error_code=state.value
                ).state
        LOGGER.info("job.finish run_id=%s state=%s", invocation.run_id, state)
    except Exception:
        LOGGER.exception("job.failed run_id=%s", invocation.run_id)
        try:
            runner.commit_unsuccessful(
                invocation, state=RunState.FAILED, error_code="job_failed"
            )
        except Exception:
            LOGGER.exception("job.failure_commit_failed run_id=%s", invocation.run_id)
        return 1
    return 0


def main() -> int:
    return asyncio.run(run())


if __name__ == "__main__":
    raise SystemExit(main())
