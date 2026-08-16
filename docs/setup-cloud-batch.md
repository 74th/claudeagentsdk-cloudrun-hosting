# Cloud Batch のセットアップ

## この構成を選ぶ場合

Cloud Batch は、マシンタイプ、CPU、メモリを明示してエージェントを実行したい場合に向いています。1 run ごとに決定的な Job ID を持つ Cloud Batch Job を作成します。Cloud Run Jobs のような静的 Job resource は作成しません。

## 前提

- 課金が有効な Google Cloud project
- Python 3.12、`uv`、Terraform 1.8 以上、Google Cloud CLI、Docker
- Vertex AI で利用する Claude model へのアクセスと quota
- 対象 project へ Terraform を適用できる権限
- 対象 project の実行サービスアカウントが pull できるレジストリへ push 済みの Job image

## 1. Job image を用意する

`example/agent/runtime.py` を用途に合わせて変更し、Job image を build・push します。

```bash
gcloud auth configure-docker <REGION>-docker.pkg.dev
docker build -f example/Dockerfile -t <IMAGE_URI>:<TAG> .
docker push <IMAGE_URI>:<TAG>
```

本番用 release では digest で image を固定することを推奨します。

## 2. release config を作る

```bash
cp release.batch.example.yaml release.batch.production.yaml
```

環境固有値と、Cloud Batch の計算資源を設定します。

```yaml
schema_version: "4"
execution_platform: cloud-batch
enable_cloud_run: false
enable_cloud_batch: true
enable_gke: false
project_id: <PROJECT_ID>
region: <REGION>
firestore_location: <REGION>
firestore_database: <NAMED_DATABASE>
bucket_name: <GLOBALLY_UNIQUE_BUCKET_NAME>
image: <IMAGE_URI>@sha256:<DIGEST>
cloud_batch:
  job_id_prefix: claude-agent
  machine_type: e2-standard-2
  cpu_milli: 2000
  memory_mib: 4096
```

`job_id_prefix` は小文字から始まる 25 文字以内の値にします。release config に秘密値は記述しません。Job へ渡す application 引数は `RUN_ID` だけで、Firestore、GCS、Vertex AI へのアクセスにはサービスアカウントを利用します。

## 3. plan と apply を実行する

```bash
gcloud auth application-default login
uv run python scripts/deploy.py release.batch.production.yaml
uv run python scripts/deploy.py release.batch.production.yaml --apply
```

Terraform は Cloud Batch API、名前付き Firestore database、GCS bucket、サービスアカウント、control plane が Batch Job を作成するための IAM を構成します。実際の Batch Job はフロントエンドから run が開始された時に作られます。

## 4. フロントエンドを接続する

フロントエンドは `execution_platform: cloud-batch` の release config から `ChatService` または `ControlClient` を構成します。フロントエンドの principal には Firestore へのアクセス、Batch Job の作成・取消、Job 用サービスアカウントを利用する権限が必要です。

## 運用確認

```bash
gcloud batch jobs list --location=<REGION> --project=<PROJECT_ID>
gcloud logging read \
  'resource.type="batch_job"' \
  --project=<PROJECT_ID> --limit=100
```

重複した開始要求は同じ run ID から同じ Batch Job ID へ収束します。キャンセル要求と Job の消失を Firestore の終端状態へ反映する流れは [アーキテクチャと Job 運用](architecture-and-job-operations.md) を参照してください。

## Cloud Run Jobs と併用して切り替える場合

`enable_cloud_run` と `enable_cloud_batch` を両方 `true` にして両方の基盤を維持しておけば、release config の `execution_platform` を変えてフロントエンドを再配備することで選択を切り替えられます。

切り替え前に active な run を完了またはキャンセルし、Terraform plan で Firestore database と GCS bucket に replace や destroy がないことを確認してください。データ層は実行基盤間で共通です。
