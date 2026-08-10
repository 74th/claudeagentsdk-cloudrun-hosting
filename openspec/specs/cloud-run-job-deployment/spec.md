# cloud-run-job-deployment Specification

## Purpose

Cloud Run Jobs、Firestore、GCS と関連 IAM を再現可能な設定から構築し、長時間エージェントジョブを最小権限で安全に配備できるようにする。

## Requirements

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

### Requirement: 制御主体とジョブ実行主体を分離する
デプロイ構成は選択した実行基盤でジョブを開始・照会・キャンセルする制御主体と、Firestore および対象 GCS 名前空間へアクセスするジョブ実行主体を分離し、それぞれへ選択した実行基盤に必要な最小限の権限だけを付与しなければならない（SHALL）。

#### Scenario: IAM設定を検査する
- **WHEN** 選択した実行基盤の Terraform plan で IAM binding を確認する
- **THEN** 制御主体とジョブ実行主体にプロジェクト全体の広域管理者権限が付与されず、無効化した実行基盤を操作する権限も付与されていない

#### Scenario: Cloud RunのIAM設定を検査する
- **WHEN** `enable_cloud_run=true` の Terraform plan で IAM binding を確認する
- **THEN** 制御主体は Cloud Run Job の操作権限を持ち、制御主体とジョブ実行主体にプロジェクト全体の広域管理者権限が付与されていない

#### Scenario: Cloud BatchのIAM設定を検査する
- **WHEN** `enable_cloud_batch=true` の Terraform plan で IAM binding を確認する
- **THEN** 制御主体は Batch Job の操作と指定したジョブ実行サービスアカウントの利用に必要な権限を持ち、広域管理者権限が付与されていない

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

### Requirement: Cloud Run Jobへ実行設定を適用する
デプロイ処理はコンテナイメージ、CPU、メモリ、task timeout、再試行回数、同時タスク数、実行サービスアカウント、必要な非秘密環境設定を Cloud Run Job へ適用しなければならない（SHALL）。プラットフォームの自動タスク再試行はエージェントの二重実行を防げる設定としなければならない（SHALL）。

#### Scenario: Job設定を更新する
- **WHEN** 利用者が実行基盤 `cloud-run` の検証済みリリース設定でデプロイする
- **THEN** Cloud Run Job は指定された実行制限とサービスアカウントを持つ revision へ更新される

### Requirement: Firestoreのクエリ要件を配備する
デプロイ構成はリリース設定で指定された名前付きFirestore Native databaseの mode、location、index を明示し、アプリケーション設定との不一致を拒否しなければならない（SHALL）。デプロイ構成は `(default)` databaseを作成または参照してはならない（MUST NOT）。

#### Scenario: セッション一覧用indexを計画する
- **WHEN** 有効な名前付きFirestore databaseを指定してTerraform plan を作成する
- **THEN** セッションを更新日時順にページングするために必要な index が、その指定databaseを対象として計画へ含まれる

#### Scenario: サンプルdatabaseを新規環境へ適用する
- **WHEN** `claude-agent-chat` を指定した有効な環境設定で Terraform を適用する
- **THEN** Firestore Native database `claude-agent-chat` と必要な index が作成され、`(default)` databaseはこの構成によって作成または変更されない

### Requirement: 保存データの保持期限を構成する
デプロイ構成は Firestore のセッション、run、イベントと、GCS の workspace・transcript・一時 object に共通する保持期間をリリース設定で変更可能にし、既定値と対象を配備前に表示しなければならない（SHALL）。共通保持期間の既定値は基準時刻またはオブジェクト作成時刻から 30 日とし、名前付き Firestore database のセッション、run、イベント各 collection group には有効期限フィールドによる自動削除を、GCS bucket には同じ日数の lifecycle による自動削除を構成しなければならない（SHALL）。Firestore と GCS に異なる通常保持期間を設定できてはならない（MUST NOT）。

#### Scenario: 既定保持期間で計画する
- **WHEN** 利用者がリリース設定で保持期間を省略する
- **THEN** システムは Firestore のセッション・run・イベントと GCS の全保存 object に共通する 30 日の保持期間を plan 前に表示する

#### Scenario: Firestore TTLを計画する
- **WHEN** 有効な名前付き Firestore database と GCS bucket を指定して Terraform plan を作成する
- **THEN** plan はその database のセッション、run、イベント各 collection group に同じ有効期限フィールドの TTL ポリシーを含み、bucket の全保存 object に同じ保持日数の削除 lifecycle を含む

#### Scenario: リリース設定で保持期間を変更する
- **WHEN** 利用者がリリース設定の共通保持期間へ有効な日数を指定する
- **THEN** Terraform、Cloud Run Job、Firestore TTL 対象ドキュメントの有効期限、および GCS lifecycle に同じ日数が渡される

#### Scenario: 親セッションが先に削除される
- **WHEN** TTL によりセッション親ドキュメントが run またはイベントより先に物理削除される
- **THEN** run とイベントも各自の TTL ポリシーにより自動削除の対象であり、親削除だけに依存して無期限に残留しない

### Requirement: 秘密情報を成果物へ埋め込まない
リリース設定、Terraform 変数、コンテナイメージ、ログへ API key、サービスアカウント鍵、アクセストークンを埋め込んではならない（MUST NOT）。

#### Scenario: 秘密情報らしい設定を指定する
- **WHEN** 利用者が禁止された秘密情報項目をリリース設定へ記述する
- **THEN** 検証はクラウド変更前に設定を拒否する

### Requirement: Cloud Batchへ実行設定を適用する
実行基盤 `cloud-batch` を選択した場合、デプロイ構成と制御側はコンテナイメージ、machine type、CPU／メモリ、task timeout、再試行回数、実行サービスアカウント、必要な非秘密環境設定から 1 run ごとの Batch Job を作成できなければならない（SHALL）。自動タスク再試行はエージェントの二重実行を防ぐため 0 でなければならず（MUST）、task count と parallelism は 1 でなければならない（MUST）。

#### Scenario: Batch Job設定を組み立てる
- **WHEN** 利用者が実行基盤 `cloud-batch` の検証済みリリース設定で run を開始する
- **THEN** 制御側は指定された image、計算資源、timeout、サービスアカウント、共通の Firestore／GCS 設定、run ID を持つ 1 task の Batch Job を作成する

### Requirement: runtime backend と Terraform enable を分離する
リリース設定は `execution_platform` で実行 backend を正確に 1 つ選択し、Terraform は `enable_cloud_run`／`enable_cloud_batch` で利用可能な基盤を独立して管理しなければならない（SHALL）。選択した runtime backend が Terraform で無効な場合、設定検証は cloud change 前に失敗しなければならない（SHALL）。実行基盤を省略した既存 schema version 2 設定は Cloud Run として移行可能でなければならない（SHALL）。

#### Scenario: YAMLだけでCloud RunからCloud Batchへ切り替える
- **WHEN** `enable_cloud_run=true` と `enable_cloud_batch=true` の Terraform 適用後、利用者が YAML の `execution_platform` を `cloud-run` から `cloud-batch` へ変更する
- **THEN** Terraform apply なしに composition root は Batch backend を選択し、Firestore database と GCS bucket は同じ保存データを利用する

#### Scenario: 旧設定を移行する
- **WHEN** 実行基盤項目を持たない schema version 2 の設定を新 schema へ更新する
- **THEN** 移行手順は `cloud-run` を明示するよう案内し、既存 Cloud Run の実行動作を維持する

### Requirement: Cloud Batchの設定例を提供する
プロジェクトは秘密情報を含まない Cloud Batch 用の完全なリリース設定例と、検証、Terraform plan、適用、run 起動、状態確認、キャンセルの手順を提供しなければならない（SHALL）。

#### Scenario: exampleで配備を計画する
- **WHEN** 利用者が Cloud Batch example のプレースホルダーを対象環境の値へ置き換えてデプロイコマンドを実行する
- **THEN** 設定検証を通過し、Cloud Batch を選択した Terraform plan と有効になる API／IAM／共通データ基盤が表示される
