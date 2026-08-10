## ADDED Requirements

### Requirement: GKE Jobへ実行設定を適用する
実行基盤 `gke` を選択した場合、デプロイ構成と制御側は `claude-agent` namespace、Kubernetes ServiceAccount、コンテナイメージ、CPU／メモリ、active deadline、再試行回数、必要な非秘密環境設定から 1 run ごとの Kubernetes Job を作成できなければならない（SHALL）。Job の `backoffLimit` はエージェントの二重実行を防ぐため 0 でなければならず（MUST）、並列 Pod 数と完了数は 1 でなければならない（MUST）。

#### Scenario: GKE Job設定を組み立てる
- **WHEN** 利用者が実行基盤 `gke` の検証済みリリース設定で run を開始する
- **THEN** 制御側は指定された image、計算資源、timeout、KSA、共通の Firestore／GCS 設定、run ID を持つ単一 Pod の Job を `claude-agent` namespace に作成する

### Requirement: GKE Workload IdentityをKSA principalで構成する
Terraform 構成は GKE Job 用 Kubernetes ServiceAccount を Workload Identity Federation for GKE の principal URI として直接指定し、アクセス対象リソース単位で必要最小限の IAM role を付与しなければならない（SHALL）。principal URI の `projects` には Project Number、workload identity pool には Project ID、subject には対象 namespace と KSA 名を使用しなければならない（MUST）。専用 GCP Service Account、サービスアカウント JSON 鍵、`roles/iam.workloadIdentityUser` binding、`iam.gke.io/gcp-service-account` annotation を作成してはならない（MUST NOT）。

#### Scenario: GKE用IAMを計画する
- **WHEN** `enable_gke=true` の Terraform plan で GKE Job 用 IAM binding を確認する
- **THEN** member は `claude-agent` namespace の KSA を表す `principal://iam.googleapis.com/...` URI であり、GCS は対象 bucket 単位、リソース単位に付与できない権限だけが project 単位で付与される

#### Scenario: Kubernetes ServiceAccountを検査する
- **WHEN** Terraform が作成した GKE Job 用 KSA を確認する
- **THEN** KSA に GCP Service Account の annotation はなく、Pod は ADC を通じて GKE Metadata Server から認証情報を取得できる

## MODIFIED Requirements

### Requirement: 必要なGoogle Cloud基盤をTerraformで構築する
Terraform 構成は `enable_cloud_run`／`enable_cloud_batch`／`enable_gke` に応じて Cloud Run Job、Google Cloud Batch、GKE Job の実行基盤を構成し、いずれかを有効化した場合も Firestore database と必要な index、GCS bucket、Artifact Registry、選択した基盤に必要な主体、共通 IAM を作成または参照しなければならない（SHALL）。無効化した側の実行リソースおよび専用 IAM は作成してはならない（MUST NOT）。release YAML の `execution_platform` は runtime backend を選択するが、Terraform の enable flag を暗黙に変更してはならない。

#### Scenario: 新規環境へ適用する
- **WHEN** 利用者が1つ以上の実行基盤を enable した有効な環境設定で Terraform を適用する
- **THEN** enable したジョブ実行基盤と、チャット制御およびジョブ実行に必要な共通 Google Cloud リソースが同じ設定に基づいて構築される

#### Scenario: Cloud Run環境へ適用する
- **WHEN** 利用者が `enable_cloud_run=true` の環境設定で Terraform を適用する
- **THEN** Cloud Run Job と Cloud Run 専用 IAM、および共通 Google Cloud リソースが構築される

#### Scenario: Cloud Batch環境へ適用する
- **WHEN** 利用者が `enable_cloud_batch=true` の環境設定で Terraform を適用する
- **THEN** Batch API と Batch Job の起動に必要な IAM、および共通 Google Cloud リソースが構築される

#### Scenario: GKE環境へ適用する
- **WHEN** 利用者が `enable_gke=true` で cluster、region、namespace、KSA を指定した環境設定を Terraform に適用する
- **THEN** 対象クラスタに namespace と KSA が構築され、KSA principal に既存の Firestore、GCS、Vertex AI への必要最小限の IAM が付与される

### Requirement: バージョン付きリリース設定を検証する
システムは project、region、container image、実行基盤、実行基盤別ジョブ設定、Firestore database名、GCS、実行主体、実行制限を version 付き設定で管理し、未知の項目、不正な組み合わせ、未対応 schema version または未実装の実行基盤をクラウド変更前に拒否しなければならない（SHALL）。Firestore database名は空文字列および `(default)` を許可してはならない（MUST NOT）。GKE の cluster region は Firestore location や既存の Cloud Run／Batch region から独立して指定できなければならない（SHALL）。

#### Scenario: 不正な設定を読み込む
- **WHEN** リリース設定に未知の項目、選択した実行基盤で整合しない location、空のFirestore database名、`(default)`、または選択した実行基盤に不適合な項目がある
- **THEN** デプロイ処理は対象項目を示して失敗し、クラウドまたは Kubernetes リソースを変更しない

#### Scenario: 名前付きFirestore databaseを設定する
- **WHEN** 利用者が有効な名前付きFirestore database名を含むリリース設定を読み込む
- **THEN** そのdatabase名はTerraform入力と選択したジョブコンテナの非秘密環境設定へ同じ値で渡される

#### Scenario: Cloud Batchを選択する
- **WHEN** 利用者が実行基盤 `cloud-batch` と有効な Batch 設定を指定する
- **THEN** 検証済み設定は Batch バックエンドの接続情報と Terraform 入力へ変換される

#### Scenario: GKEを選択する
- **WHEN** 利用者が実行基盤 `gke` と cluster、cluster region、namespace、KSA、Job 設定を指定する
- **THEN** 検証済み設定は GKE バックエンドの接続情報と Terraform 入力へ変換される

#### Scenario: 未実装のGKE Podを選択する
- **WHEN** 利用者が実行基盤 `gke` を指定したが、実装済み schema が要求する GKE 固有設定を指定していない
- **THEN** 検証は不足する GKE 設定を示してクラウド変更前に失敗する

#### Scenario: 選択した基盤が無効である
- **WHEN** `execution_platform` に指定した実行基盤の enable flag が false である
- **THEN** 検証は設定の不整合を示してクラウド変更前に失敗する
