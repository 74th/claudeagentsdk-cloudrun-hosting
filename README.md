# Claude Agent SDK on Cloud Run Jobs

1 run は 1 Cloud Run Execution として起動します。Control plane は Firestore の user / session / run / event を正本とし、workspace と Claude transcript は GCS の不変 snapshot として保存します。

公開 provider port は `ExecutionBackend`、`ChatStore`、`WorkspaceStore` です。Cloud Run Execution は at-least-once で起動され得ますが、JobRunner の owner claim により Claude 実行は同時に 1 件へ制限します。

識別子は user ID、session ID、run ID、execution reference、Claude session ID、workspace ID を分離します。Firestore は `users/{user-hash}/sessions/{session}/runs/{run}/events/{event}` を保存し、event は `(sequence, event_id)` で順序付けます。run は `requested → dispatching → pending → running` から `completed`、`failed`、`cancelled`、`timed_out`、`dispatch_failed` のいずれかへ遷移します。

## テストと配備

```bash
uv run pytest
terraform -chdir=terraform init -backend=false
terraform -chdir=terraform validate
python scripts/deploy.py release.example.yaml
```

Cloud Run Job image は `example/Dockerfile` で build し、release 設定の image を更新して配備します。`release.example.yaml` は名前付き Firestore database `claude-agent-chat` を作成・利用します。Job は `RUN_ID` だけを受け取り、入力・イベント・cancel flag は Firestore から取得します。Streamlit sample は同じ release 設定（テスト環境は `nnyn-dev`）を接続設定として使用します。ローカル ADC を準備してから起動してください。

この構成はプロジェクトの `(default)` database を作成・利用・変更しません。また、既存の `(default)` 内データを `claude-agent-chat` へ自動移行しません。既存データが必要な場合は、別途承認した移行手順を実施してください。

Claude の推論には Vertex AI を使用します。既定は `us-east5` の Claude Haiku 4.5（`claude-haiku-4-5@20251001`）です。Cloud Run Job 自体は `us-central1` のままで、Job のサービスアカウントへ Vertex AI User 権限を付与します。API key は使用しません。

```bash
gcloud auth application-default login
uv sync --group streamlit
uv run streamlit run sample_frontend/app.py
```

サイドバーで `Release config` と `User ID` を指定すると、Firestore の session / run / event と Cloud Run Job の開始・cancel へ接続します。

Cloud Run Job の実行状況は Cloud Logging で確認できます。`job.start`、`job.claim.acquired`、`claude_sdk.query.start`、SDK message ごとの `job.events.persisted`、終端の `job.finish` または `job.failed` を出力します。prompt や tool payload はログ出力しません。

```bash
gcloud logging read \
  'resource.type="cloud_run_job" AND resource.labels.job_name="test-claudesdk-cloudrun"' \
  --project=nnyn-dev --limit=100 --format='value(textPayload)'
```

テスト用リリースは `terraform/test.tfvars` を確認して `terraform -chdir=terraform apply -var-file=test.tfvars` を実行します。Streamlit で session を作成し、メッセージを送信して event を再訪・購読し、必要なら Cancel を選びます。Job の失敗や Execution 消失は status 確認時の reconciler で補正します。画面を開いていない Run を継続監視する場合は、同じ `ControlClient.reconcile(run_id, holder=...)` 契約を呼ぶ外部ポーラーを運用します。

snapshot 容量、GCS retention、Job timeout、retry、IAM、DEBUG logging は `release.example.yaml` と Terraform で設定します。workspace はコンテナ内で sandbox 化されず、backend と Firestore の状態が不整合になった場合は reconciler が補正します。

DEBUG log は prompt や tool payload を出力しない運用にし、snapshot は容量上限と retention を超えると削除対象になります。Cloud Run task retry は 0 が既定で、timeout、最小権限 IAM、Firestore / GCS の保持期間を変更する場合は release 設定と Terraform plan を確認してください。

## Firestore TTL と legacy データの移行

`run_retention_days` は session、run、event に共通する既定 30 日の保持期間です。Firestore TTL は期限到達後すぐに物理削除されるとは限らないため、アプリケーションの get/list/history も `expires_at` を検査します。Firestore の親削除は run と event の subcollection を連鎖削除しないため、3 つの collection group それぞれに TTL policy を設定しています。

既存文書の補完は、対象 project と名前付き database を明示した次の command で dry-run してから行います。

```sh
uv run python scripts/backfill_firestore_ttl.py --project PROJECT --database DATABASE
uv run python scripts/backfill_firestore_ttl.py --project PROJECT --database DATABASE --apply
```

apply 前に Firestore export を取得し、dry-run の対象件数と期限を確認してください。Terraform の TTL policy を適用した後、export、dry-run、内容確認、bounded batch の apply の順で実行します。TTL で削除されたデータはアプリケーションのロールバックでは復元できないため、復旧時は事前 export を別 database へ復元して検証後に切り替えます。

Agent Platform の ASGI runtime / Sessions / Operations 契約は廃止され、旧 session を自動移行しません。

## Migration note

Agent Platform の長時間 Operation がコンテナへ到達しない問題を避けるため、実行基盤を Cloud Run Jobs に置換しました。安全な archive、temporary workspace、Claude SDK adapter の provider 非依存部分は再利用しています。一方で Agent Platform runtime API、Sessions、Operations、Gateway/Registry 前提は互換ではなく、旧 session の自動移行は行いません。
