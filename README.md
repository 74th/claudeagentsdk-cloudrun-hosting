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

## Cloud Batch へ切り替える

Terraform は検証用に Cloud Run と Cloud Batch を両方有効化します。`release.batch.example.yaml` をコピーし、`image` を実際の digest に置き換えて設定ファイルを差し替えるだけで Batch backend を選択できます。秘密情報は release 設定へ記述しません。基盤を無効化したい場合だけ `enable_cloud_run` / `enable_cloud_batch` を変更して Terraform を再適用します。

```bash
uv run python scripts/deploy.py release.batch.example.yaml
uv run python scripts/deploy.py release.batch.example.yaml --apply
```

`release.example.yaml` に戻すと Cloud Run backend に戻ります。両方の enable flag が true の間はこの切替に Terraform apply は不要です。

Cloud Batch は 1 run を 1 Job として作成し、コンテナへは `RUN_ID` と Firestore／GCS／Vertex AI の非秘密設定だけを渡します。Job の状態は Firestore の run と Cloud Batch の Job を確認します。

```bash
gcloud batch jobs list --location=us-central1 --project=nnyn-dev
gcloud logging read \
  'resource.type="batch_job"' --project=nnyn-dev --limit=100
```

実行中の run は UI の Cancel または `ControlClient.cancel(run_id)` でキャンセルします。Batch Job の削除要求後、Firestore の cancel request と Job 消失を reconciler が終端状態へ確定します。重複した開始要求は run ID から同じ Job ID へ収束します。

Cloud Run と Batch の切替前には、両方の変数で refresh なしの plan を取得し、`google_firestore_database.chat` と `google_storage_bucket.workspace` に `-/+` や destroy がないことを確認します。active な run は完了またはキャンセルしてから切り替えてください。ロールバックは `execution_platform: cloud-run` と `cloud_run.job_name` を指定した設定へ戻して plan／apply します。Firestore database と GCS bucket は共通 resource address のため削除しません。

`gke` は将来の予約値として認識しますが、今回の実装・example・Terraform 適用対象外です。指定するとクラウド変更前の設定検証で失敗します。

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

`retention_days` は Firestore の session、run、event と GCS の workspace、transcript、一時 object に共通する保持期間です。既定は 30 日で、release 設定から変更できます。`run_retention_days`、`snapshot_retention_days`、`uncommitted_retention_days` を使用している旧設定は schema version 2 では拒否されるため、単一の `retention_days` へ置き換えてください。

Firestore TTL と GCS lifecycle は期限到達後の非同期削除です。期限を過ぎても物理削除まで時間がかかる場合があるため、アプリケーションの get/list/history は `expires_at` を検査します。Firestore の親削除は run と event の subcollection を連鎖削除しないため、3 つの collection group それぞれに TTL policy を設定しています。GCS は commit 状態や prefix によらず全 object に同じ lifecycle を適用します。

期限到達後の削除を release の rollback で取り消したり、既に削除された Firestore document・GCS object を自動復旧したりすることはできません。適用前に export / plan を取得し、`python scripts/deploy.py release.example.yaml` の表示で Firestore と GCS の対象・共通日数を確認してください。

既存文書の補完は、対象 project と名前付き database を明示した次の command で dry-run してから行います。

```sh
uv run python scripts/backfill_firestore_ttl.py --project PROJECT --database DATABASE
uv run python scripts/backfill_firestore_ttl.py --project PROJECT --database DATABASE --apply
```

apply 前に Firestore export を取得し、dry-run の対象件数と期限を確認してください。Terraform の TTL policy を適用した後、export、dry-run、内容確認、bounded batch の apply の順で実行します。TTL で削除されたデータはアプリケーションのロールバックでは復元できないため、復旧時は事前 export を別 database へ復元して検証後に切り替えます。

Agent Platform の ASGI runtime / Sessions / Operations 契約は廃止され、旧 session を自動移行しません。

## Migration note

Agent Platform の長時間 Operation がコンテナへ到達しない問題を避けるため、実行基盤を Cloud Run Jobs に置換しました。安全な archive、temporary workspace、Claude SDK adapter の provider 非依存部分は再利用しています。一方で Agent Platform runtime API、Sessions、Operations、Gateway/Registry 前提は互換ではなく、旧 session の自動移行は行いません。
