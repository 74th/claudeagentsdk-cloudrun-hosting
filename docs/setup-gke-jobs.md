# GKE Jobs のセットアップ

## この構成を選ぶ場合

GKE Jobs は、既存の GKE クラスター、NodePool、Kubernetes の監視・運用基盤を利用したい場合に向いています。1 run ごとに Kubernetes Job を作成します。このリポジトリの Terraform は GKE クラスターを作成せず、既存クラスター内の namespace、Kubernetes service account（KSA）、必要な IAM を管理します。

## 前提

- Workload Identity Federation for GKE が有効な既存 GKE クラスター
- Python 3.12、`uv`、Terraform 1.8 以上、Google Cloud CLI、`kubectl`、Docker
- Vertex AI で利用する Claude model へのアクセスと quota
- 対象 project と GKE クラスターへ Terraform を適用できる権限
- GKE node が pull できるレジストリへ push 済みの Job image

## 1. kubeconfig context を用意する

```bash
gcloud container clusters get-credentials <CLUSTER_NAME> \
  --region=<CLUSTER_REGION> --project=<PROJECT_ID>
kubectl config current-context
kubectl config get-contexts
```

Terraform とフロントエンドは、この context を使って namespace や Job を操作します。release YAML や Terraform state に kubeconfig の内容、鍵、token は保存しません。

## 2. Job image を用意する

`example/agent/runtime.py` を用途に合わせて変更し、Job image を build・push します。

```bash
gcloud auth configure-docker <REGION>-docker.pkg.dev
docker build -f example/Dockerfile -t <IMAGE_URI>:<TAG> .
docker push <IMAGE_URI>:<TAG>
```

## 3. release config を作る

```bash
cp release.gke.yaml release.gke.production.yaml
```

環境固有値と既存クラスターの情報を設定します。

```yaml
schema_version: "4"
execution_platform: gke
enable_cloud_run: false
enable_cloud_batch: true
enable_gke: true
project_id: <PROJECT_ID>
region: <REGION>
firestore_location: <REGION>
firestore_database: <NAMED_DATABASE>
bucket_name: <GLOBALLY_UNIQUE_BUCKET_NAME>
image: <IMAGE_URI>@sha256:<DIGEST>
gke:
  cluster: <CLUSTER_NAME>
  cluster_region: <CLUSTER_REGION>
  namespace: claude-agent
  ksa_name: claude-agent
  kube_context: <KUBECONFIG_CONTEXT>
  cpu: "1"
  memory: 2Gi
  job_ttl_seconds: 3600
```

現行 Terraform ではフロントエンド用 control service account も作成するため、GKE のみで実行する場合も `enable_cloud_run` または `enable_cloud_batch` の少なくとも一方を `true` にします。`execution_platform: gke` なので、run 自体は GKE Jobs で実行されます。

taint のある NodePool へ配置できるようにする場合は toleration を追加できます。

```yaml
gke:
  # ほかの設定は省略
  tolerations:
    - key: dedicated
      operator: Exists
      value: ""
      effect: NoSchedule
```

`operator` は `Equal` または `Exists`、`effect` は `NoSchedule`、`PreferNoSchedule`、`NoExecute` です。`Exists` の場合は `value` を空文字列にします。toleration は taint を許容するだけなので、特定の NodePool を選ぶにはクラスター側の scheduling 方針も合わせて設計してください。

## 4. plan と apply を実行する

```bash
gcloud auth application-default login
uv run python scripts/deploy.py release.gke.production.yaml
uv run python scripts/deploy.py release.gke.production.yaml --apply
```

Terraform は既存クラスターに namespace と KSA を作成し、その KSA principal へ Firestore、GCS、Vertex AI の権限を直接付与します。GSA key や KSA annotation は使用しません。

## 5. フロントエンドを接続する

フロントエンドは Firestore に加え、対象クラスターへ Kubernetes Job を作成・取得・削除できる必要があります。ローカル kubeconfig に依存させる構成は動作確認向けです。本番では、フロントエンドの実行場所、クラスター API へのネットワーク到達性、Kubernetes RBAC と Google Cloud IAM を明示的に設計してください。

## 運用確認

```bash
kubectl -n claude-agent get serviceaccount,jobs,pods
kubectl -n claude-agent describe job <JOB_NAME>
kubectl -n claude-agent logs job/<JOB_NAME>
```

Job 完了後は `job_ttl_seconds` に従って Kubernetes Job が削除されます。永続的な会話履歴と run 状態は Firestore、workspace は GCS に残ります。

## 他の実行基盤から切り替える場合

Cloud Run Jobs または Cloud Batch と GKE を同時に有効化しておけば、共有する Firestore と GCS を維持したまま実行基盤を切り替えられます。active な run を完了またはキャンセルした後、`execution_platform: gke` の release config で Terraform plan を確認し、フロントエンドを同じ config で再配備します。

plan では Firestore database と GCS bucket に replace や destroy がないことを確認してください。元へ戻す場合は `execution_platform` を `cloud-run` または `cloud-batch` にした release config へ戻します。GKE 専用の namespace、KSA、IAM も削除する場合は、active な Kubernetes Job がないことを確認してから `enable_gke: false` を適用します。
