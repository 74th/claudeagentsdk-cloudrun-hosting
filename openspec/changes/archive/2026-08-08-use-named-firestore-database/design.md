## Context

See [proposal.md](proposal.md) for the motivation. The current Terraform resource, index definitions, Streamlit factory, live tests, and the example Job default independently name `(default)`. The release configuration has Firestore location but no database field, while the Job supports a `FIRESTORE_DATABASE` environment variable with `(default)` as its fallback.

## Goals / Non-Goals

**Goals:**

- Make a validated `firestore_database` release setting the single source of truth for database selection.
- Provision and use `claude-agent-chat` for the sample without depending on `(default)`.
- Ensure Terraform's database and indexes, the control client, and the Job agree on the selected database.

**Non-Goals:**

- Rename, delete, or migrate data from an existing `(default)` database.
- Change collection paths, Firestore security model, retention policy, or the public ChatStore contract.
- Support selecting a separate database for every user or session.

## Decisions

### Use an explicit required release setting

Add `firestore_database` to `ReleaseConfig`, require a non-empty valid database ID, and reject `(default)`. The same value is emitted as a Terraform variable and used by the Streamlit factory. This prevents a partial configuration from silently reconnecting to the shared default database.

Alternative considered: retain `(default)` as a compatibility default. Rejected because it defeats isolation when a release file is not updated.

### Create the named database through Terraform and inject it into the Job

Replace the Terraform database resource name value with the `firestore_database` variable and bind every Firestore index to that resource. Add `FIRESTORE_DATABASE` to the Job's non-secret environment variables. The Job entrypoint treats this environment variable as required rather than falling back to `(default)`.

Alternative considered: hard-code `claude-agent-chat` in every component. Rejected because the deployment configuration would cease to be a consistent, reusable source of truth.

### Keep existing default-database data isolated

This change provisions a distinct database and starts new sessions there. It does not import old documents, because the application data does not need to be preserved for the sample and migration policy depends on the deployment owner.

Alternative considered: copy data during Terraform apply. Rejected because Terraform is not an application-data migration mechanism and such copying could be destructive or incomplete.

## Risks / Trade-offs

- [A project already has a non-compatible Firestore database configuration or cannot create another database] → Terraform plan/apply reports the provider error before application traffic is switched; verify the plan in the target project.
- [An old Job revision or manually invoked container lacks `FIRESTORE_DATABASE`] → fail at startup rather than write to `(default)`.
- [Live integration tests still select `(default)`] → parameterize their database with an opt-in environment variable and skip/fail clearly if it is absent.
- [Existing default-database data is expected] → keep the old database untouched and perform a separately approved export/import migration if required.

## Migration Plan

1. Add and validate `firestore_database: claude-agent-chat` to the sample release configuration.
2. Run tests and Terraform plan to confirm the named database, indexes, and Job environment variable target the same ID.
3. Apply to create `claude-agent-chat`, then deploy the new Job revision and connect the UI with the updated release file.
4. Verify newly created sessions and events appear only in `claude-agent-chat`.
5. During the stateful upgrade, remove the legacy `(default)` database and index resources from Terraform state with `destroy = false`, then create separate named-database index resources. This prevents the plan from deleting legacy indexes.
6. Roll back the application revision and release configuration if needed; do not delete or mutate either database or its legacy indexes as part of rollback.
