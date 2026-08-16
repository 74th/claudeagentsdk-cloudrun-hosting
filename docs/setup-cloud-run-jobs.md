# Cloud Run Jobs のセットアップ

## この構成を選ぶ場合

Cloud Run Jobs は、クラスターや VM を管理せずにエージェントを実行したい場合の基本構成です。1 run は、あらかじめ Terraform で作成した 1 つの Cloud Run Job の Execution として起動します。

## 前提

- 課金が有効な Google Cloud project
- Python 3.12、`uv`、Terraform 1.8 以上、Google Cloud CLI、Docker
- Vertex AI で利用する Claude model へのアクセスと quota
- 対象 project へ Terraform を適用できる権限
- 対象 project の実行サービスアカウントが pull できるレジストリへ push 済みの Job image

このリポジトリの Terraform は Artifact Registry repository も作成しますが、release YAML の `image` は Terraform 適用時点で pull 可能な image を指定してください。新規 project の初回 image は、既存の利用可能なレジストリで先に build・push しておくと循環を避けられます。

## 1. Job image を用意する

用途に合わせた system prompt、tools、workspace 設定を `example/agent/runtime.py` へ反映してから build します。

```bash
gcloud auth configure-docker <REGION>-docker.pkg.dev
docker build -f example/Dockerfile -t <IMAGE_URI>:<TAG> .
docker push <IMAGE_URI>:<TAG>
```

本番用 release では、再現性のため `image` に tag ではなく digest を指定することを推奨します。

## 2. release config を作る

`release.example.yaml` を環境ごとのファイルへコピーし、少なくとも project、region、Firestore database、bucket、image、Job 名を変更します。

```bash
cp release.example.yaml release.production.yaml
```

Cloud Run Jobs を選択する主要項目は次のとおりです。

```yaml
schema_version: "4"
execution_platform: cloud-run
enable_cloud_run: true
enable_cloud_batch: false
enable_gke: false
project_id: <PROJECT_ID>
region: <REGION>
firestore_location: <REGION>
firestore_database: <NAMED_DATABASE>
bucket_name: <GLOBALLY_UNIQUE_BUCKET_NAME>
image: <IMAGE_URI>@sha256:<DIGEST>
cloud_run:
  job_name: <JOB_NAME>
```

`firestore_database` には `(default)` ではなく名前付き database を指定します。release config に token、password、API key などの秘密値は記述しません。推論には Job のサービスアカウントと Vertex AI を利用します。

## 3. plan と apply を実行する

Application Default Credentials を用意し、まず plan を確認します。

```bash
gcloud auth application-default login
uv run python scripts/deploy.py release.production.yaml
uv run python scripts/deploy.py release.production.yaml --apply
```

Terraform は必要な API、Cloud Run Job、名前付き Firestore database、GCS bucket、Artifact Registry、サービスアカウント、IAM を管理します。

## 4. フロントエンドを接続する

フロントエンドの実行環境に `release.production.yaml` と同じ非秘密設定を渡し、`example.chat.ChatService` または `ControlClient` を構成します。フロントエンドの principal には、Terraform が作成する control service account 相当の Firestore と Cloud Run Jobs の権限が必要です。

ローカルで接続を確認する場合は、Streamlit サンプルを利用できます。

```bash
uv sync --group streamlit
uv run streamlit run example/streamlit_frontend/app.py
```

## 運用確認

```bash
gcloud run jobs executions list --job=<JOB_NAME> --region=<REGION> --project=<PROJECT_ID>
gcloud logging read \
  'resource.type="cloud_run_job" AND resource.labels.job_name="<JOB_NAME>"' \
  --project=<PROJECT_ID> --limit=100
```

run の正本は Firestore です。Cloud Run Execution が失敗または消失した場合の補正方法は [アーキテクチャと Job 運用](architecture-and-job-operations.md) を参照してください。
