## 1. Domain and control contracts

- [ ] 1.1 Add provider-neutral initial-session result and paged run-history models, deterministic session/workspace/run ID derivation, and first-prompt title normalization with unit tests for whitespace, Unicode truncation, and stable retries.
- [ ] 1.2 Extend `ChatStore` with atomic initial-run reservation and stable session run paging while preserving the existing session and subsequent-run methods.
- [ ] 1.3 Add ControlClient operations that start a named session from the first prompt, reuse the existing dispatch/failure lifecycle, and expose ordered run history; cover successful, retried, and dispatch-failed starts.

## 2. Store implementations and retention behavior

- [ ] 2.1 Implement atomic first-session reservation, deterministic idempotency, run paging, and ownership checks in `InMemoryChatStore`, then add shared contract tests for duplicate-free starts and multi-run ordering.
- [ ] 2.2 Extend the Firestore codec/adapter with configurable clock and retention, storage-only Timestamp `expires_at` fields for session/run/event writes, compatible decoding of legacy documents, and filtering of expired get/list/history results.
- [ ] 2.3 Implement the Firestore transaction that creates the session, first run, and user event together, including retry behavior when the deterministic session already exists.
- [ ] 2.4 Implement Firestore run-history paging by `(created_at, id)`, retain `(sequence, event_id)` event ordering, and add fake-client tests for cursor stability, multiple runs, expired-document overfetch, and active-run restoration.
- [ ] 2.5 Refresh retention timestamps on every mutable session/run write path and verify immutable event expiry, terminal transitions, dispatch failures, and legacy documents with focused tests.

## 3. Sample UI session experience

- [ ] 3.1 Update `ChatViewModel` to start a session from the first prompt and load every run/event page for a selected session without exposing provider-specific store objects.
- [ ] 3.2 Replace eager New session creation with a Streamlit draft state that has no ID, retains its initial idempotency key across reruns, and selects the persisted session only after the first prompt is accepted.
- [ ] 3.3 Render session list labels as UTC final-update time plus title in server-provided descending order, using `Untitled session` rather than IDs for legacy blank titles.
- [ ] 3.4 Render the current title and separately labeled Session ID, Run ID, and adjacent Cloud Run execution ID, including the pending-execution state.
- [ ] 3.5 Render all displayable events across historical runs in conversation order, then continue existing refresh/subscription behavior for the active run; add UI/view-model tests for empty draft, multiple-run revisit, and identifier display.

## 4. Firestore TTL deployment and migration

- [ ] 4.1 Wire the existing `run_retention_days` setting through release validation, factory settings, Terraform variables, and Cloud Run/control configuration as the shared session/run/event retention value, keeping the 30-day default.
- [ ] 4.2 Add Terraform `expires_at` TTL field policies for the `sessions`, `runs`, and `events` collection groups in the selected named database, plus the run-history index, and extend deployment validation tests.
- [ ] 4.3 Add an explicit-project, explicit-database, dry-run-by-default backfill command that computes missing `expires_at` values from legacy timestamps and updates documents in bounded idempotent batches; unit-test timestamp selection and batch behavior.
- [ ] 4.4 Document the TTL deletion delay, non-cascading subcollections, required pre-backfill Firestore export, dry-run review, apply order, and irreversible-deletion rollback procedure.

## 5. Verification

- [ ] 5.1 Run the unit and integration test suites covering models, both ChatStore implementations, ControlClient, sample frontend, release configuration, and deployment validation.
- [ ] 5.2 Run formatting, lint/type checks, Terraform formatting/validation, and strict OpenSpec validation; record any credential-dependent live Firestore checks as opt-in verification steps.
