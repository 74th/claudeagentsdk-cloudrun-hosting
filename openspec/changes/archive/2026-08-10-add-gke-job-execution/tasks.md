## 1. GKE実行バックエンド

- [x] 1.1 Kubernetes Python client を依存関係へ追加し、既存 kubeconfig/context から Batch API client を構築する境界を実装する
- [x] 1.2 run ID 由来の決定的な名前、label、単一実行設定、KSA、resource、timeout、非秘密環境変数を持つ GKE Job manifest の組み立てを実装する
- [x] 1.3 Job の作成と HTTP 409 時の同一 run 回収を実装し、namespace/Job 名を共通の実行参照として返す
- [x] 1.4 Job condition と counter の共通状態への正規化、および Kubernetes API の not-found、permission、conflict、quota、temporary エラー分類を実装する
- [x] 1.5 foreground deletion と削除完了確認によるキャンセルを実装し、永続化されたキャンセル状態と予期しない Job 消失を lifecycle で区別する

## 2. 設定とアプリケーション統合

- [x] 2.1 リリース schema を更新し、`enable_gke` と cluster、cluster region、namespace、KSA、context、CPU、memory、Job TTL を持つ `gke` block を追加する
- [x] 2.2 `execution_platform: gke` と enable flag の整合、GKE 固有項目、独立した cluster region、禁止秘密情報をクラウド変更前に検証する
- [x] 2.3 Google Cloud settings、client composition、factory を拡張し、frontend が GKE backend を選択して既存 Firestore/GCS control plane と組み合わせられるようにする
- [x] 2.4 project `nnyn-dev`、cluster `autopilot`、cluster region `asia-northeast1`、namespace `claude-agent` を指定する `release.gke.yaml` を追加する

## 3. TerraformとWorkload Identity

- [x] 3.1 Terraform に `enable_gke`、cluster、cluster region、namespace、KSA、kube context と GKE Job resource 設定の変数・validation を追加する
- [x] 3.2 既存 kubeconfig/context を使用する Kubernetes provider と、`enable_gke=true` の場合だけ作成する `claude-agent` namespace／KSA を追加する
- [x] 3.3 Google project data から Project Number を取得し、KSA の直接 principal URI を組み立て、対象 bucket と必要な project resource に最小権限 IAM member を付与する
- [x] 3.4 GKE 専用 GSA、JSON key、`roles/iam.workloadIdentityUser`、KSA の GSA annotation が plan に含まれないことを検証する
- [x] 3.5 Cloud Run、Cloud Batch、GKE を tfvars で独立に有効・無効化できるようにし、検証環境用 GKE tfvars を追加する

## 4. 自動テストと静的検証

- [x] 4.1 GKE Job manifest、決定的命名、重複作成、状態正規化、キャンセル、API エラー分類の単体テストを追加する
- [x] 4.2 release schema の GKE 正常系、不正組み合わせ、location 独立性、秘密情報拒否と Terraform 変換のテストを追加する
- [x] 4.3 Terraform 検証テストへ enable flag、namespace/KSA、principal URI、resource 単位 IAM、禁止された GSA impersonation 構成の不在を追加する
- [x] 4.4 全 pytest、ruff、mypy、Terraform fmt/validate と Cloud Run／Batch／GKE 各構成の plan を実行し、既存基盤の回帰がないことを確認する

## 5. 検証環境への配備

- [x] 5.1 agent コンテナイメージをビルドして Artifact Registry へ push し、新しい digest を `release.gke.yaml` と検証用 tfvars に反映する
- [x] 5.2 kubeconfig の対象が `nnyn-dev`／`asia-northeast1`／`autopilot` であることを読み取り確認し、GKE 用 Terraform plan をレビューする
- [x] 5.3 GKE 用 tfvars で Terraform apply を実行し、`claude-agent` namespace、KSA、直接 principal の IAM binding が反映されたことを確認する
- [x] 5.4 `release.gke.yaml` で frontend を構成して実 run を開始し、Job/Pod の状態遷移と ADC による Firestore、GCS、Vertex AI アクセスおよび正常完了を確認する
- [x] 5.5 実行中 run のキャンセルと同じ run の再ディスパッチを検証し、Pod 停止、`cancelled` 状態、重複 Job 不在を確認する
- [x] 5.6 README に GKE 前提条件、get-credentials、release/tfvars の切り替え、確認方法、Cloud Run／Batch への rollback 手順を記載する
