## MODIFIED Requirements

### Requirement: 必要なGoogle Cloud基盤をTerraformで構築する
Terraform 構成は `enable_cloud_run`／`enable_cloud_batch` に応じて Cloud Run Job と Google Cloud Batch の実行基盤を構成し、どちらを有効化した場合も Firestore database と必要な index、GCS bucket、Artifact Registry、サービスアカウント、共通 IAM を作成または参照しなければならない（SHALL）。無効化した側の実行リソースおよび専用 IAM は作成してはならない（MUST NOT）。release YAML の `execution_platform` は runtime backend を選択するが、Terraform の enable flag を暗黙に変更してはならない。

#### Scenario: 新規環境へ適用する
- **WHEN** 利用者が1つ以上の実行基盤を enable した有効な環境設定で Terraform を適用する
- **THEN** enable したジョブ実行基盤と、チャット制御およびジョブ実行に必要な共通 Google Cloud リソースが同じ設定に基づいて構築される

#### Scenario: Cloud Run環境へ適用する
- **WHEN** 利用者が `enable_cloud_run=true` の環境設定で Terraform を適用する
- **THEN** Cloud Run Job と Cloud Run 専用 IAM、および共通 Google Cloud リソースが構築される

#### Scenario: Cloud Batch環境へ適用する
- **WHEN** 利用者が `enable_cloud_batch=true` の環境設定で Terraform を適用する
- **THEN** Batch API と Batch Job の起動に必要な IAM、および共通 Google Cloud リソースが構築される

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
システムは project、region、container image、実行基盤、実行基盤別ジョブ設定、Firestore database名、GCS、サービスアカウント、実行制限を version 付き設定で管理し、未知の項目、不正な組み合わせ、未対応 schema version または未実装の実行基盤をクラウド変更前に拒否しなければならない（SHALL）。Firestore database名は空文字列および `(default)` を許可してはならない（MUST NOT）。

#### Scenario: 不正な設定を読み込む
- **WHEN** リリース設定に未知の項目、region の不整合、空のFirestore database名、`(default)`、または選択した実行基盤に不適合な項目がある
- **THEN** デプロイ処理は対象項目を示して失敗し、クラウドリソースを変更しない

#### Scenario: 名前付きFirestore databaseを設定する
- **WHEN** 利用者が有効な名前付きFirestore database名を含むリリース設定を読み込む
- **THEN** そのdatabase名はTerraform入力と選択したジョブコンテナの非秘密環境設定へ同じ値で渡される

#### Scenario: Cloud Batchを選択する
- **WHEN** 利用者が実行基盤 `cloud-batch` と有効な Batch 設定を指定する
- **THEN** 検証済み設定は Batch バックエンドの接続情報と Terraform 入力へ変換される

#### Scenario: 未実装のGKE Podを選択する
- **WHEN** 利用者が予約済み実行基盤 `gke` を指定する
- **THEN** 検証は GKE が未対応であることを示してクラウド変更前に失敗する

### Requirement: Cloud Run Jobへ実行設定を適用する
実行基盤 `cloud-run` を選択した場合、デプロイ処理はコンテナイメージ、CPU、メモリ、task timeout、再試行回数、同時タスク数、実行サービスアカウント、必要な非秘密環境設定を Cloud Run Job へ適用しなければならない（SHALL）。プラットフォームの自動タスク再試行はエージェントの二重実行を防げる設定としなければならない（SHALL）。

#### Scenario: Job設定を更新する
- **WHEN** 利用者が実行基盤 `cloud-run` の検証済みリリース設定でデプロイする
- **THEN** Cloud Run Job は指定された実行制限とサービスアカウントを持つ revision へ更新される

#### Scenario: Cloud Run Job設定を更新する
- **WHEN** 利用者が実行基盤 `cloud-run` の検証済みリリース設定でデプロイする
- **THEN** Cloud Run Job は指定された実行制限とサービスアカウントを持つ revision へ更新される

## ADDED Requirements

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
