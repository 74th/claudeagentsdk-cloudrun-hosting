## 1. Release configuration and application wiring

- [x] 1.1 Add a required, validated `firestore_database` field to `ReleaseConfig`; reject an empty value and `(default)`, and expose it in Terraform variables.
- [x] 1.2 Set `firestore_database: claude-agent-chat` in `release.example.yaml` and pass the configured value into the Streamlit control-client factory.
- [x] 1.3 Require `FIRESTORE_DATABASE` in the example Job entrypoint so an unset variable cannot fall back to `(default)`.

## 2. Terraform database deployment

- [x] 2.1 Add a validated `firestore_database` Terraform input and create the Firestore Native database using that name instead of `(default)`.
- [x] 2.2 Bind all Firestore indexes and fields to the named database resource, and inject its name into the Cloud Run Job as `FIRESTORE_DATABASE`.
- [x] 2.3 Update Terraform validation coverage so the named database, index targets, and Job environment variable are required by the deployment configuration.

## 3. Verification and documentation

- [x] 3.1 Update unit tests for release configuration and the sample frontend to require and propagate the named database.
- [x] 3.2 Parameterize opt-in Firestore live tests with an explicit named-database environment variable and remove default-database assumptions from test fixtures where applicable.
- [x] 3.3 Update README deployment instructions to state that `claude-agent-chat` is provisioned and that existing `(default)` data is neither used nor migrated.
- [x] 3.4 Run the relevant Python test suite and Terraform formatting/validation or plan checks; verify no deployment-path source still uses `(default)`.
