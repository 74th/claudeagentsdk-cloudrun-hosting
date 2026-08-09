# cloud-run-job-deployment Specification

## Purpose

Cloud Run Jobs、Firestore、GCS と関連 IAM を再現可能な設定から構築し、長時間エージェントジョブを最小権限で安全に配備できるようにする。

## Requirements

### Requirement: 必要なGoogle Cloud基盤をTerraformで構築する
Terraform 構成は Cloud Run Job、Firestore database と必要な index、GCS bucket、Artifact Registry、サービスアカウント、IAM を作成または参照しなければならない（SHALL）。

#### Scenario: 新規環境へ適用する
- **WHEN** 利用者が有効な環境設定で Terraform を適用する
- **THEN** チャット制御とジョブ実行に必要な Google Cloud リソースが同じ設定に基づいて構築される

### Requirement: 制御主体とジョブ実行主体を分離する
デプロイ構成はジョブを開始・照会・キャンセルする制御主体と、Firestore および対象 GCS 名前空間へアクセスするジョブ実行主体を分離し、それぞれへ必要最小限の権限だけを付与しなければならない（SHALL）。

#### Scenario: IAM設定を検査する
- **WHEN** Terraform plan の IAM binding を確認する
- **THEN** 制御主体とジョブ実行主体にプロジェクト全体の広域管理者権限が付与されていない

### Requirement: バージョン付きリリース設定を検証する
システムは project、region、container image、Cloud Run Job、Firestore database名、GCS、サービスアカウント、実行制限を version 付き設定で管理し、未知の項目、不正な組み合わせ、未対応 schema version をクラウド変更前に拒否しなければならない（SHALL）。Firestore database名は空文字列および `(default)` を許可してはならない（MUST NOT）。

#### Scenario: 不正な設定を読み込む
- **WHEN** リリース設定に未知の項目、region の不整合、空のFirestore database名、または `(default)` がある
- **THEN** デプロイ処理は対象項目を示して失敗し、クラウドリソースを変更しない

#### Scenario: 名前付きFirestore databaseを設定する
- **WHEN** 利用者が有効な名前付きFirestore database名を含むリリース設定を読み込む
- **THEN** そのdatabase名はTerraform入力とCloud Run Jobの非秘密環境設定へ同じ値で渡される

### Requirement: Cloud Run Jobへ実行設定を適用する
デプロイ処理はコンテナイメージ、CPU、メモリ、task timeout、再試行回数、同時タスク数、実行サービスアカウント、必要な非秘密環境設定を Cloud Run Job へ適用しなければならない（SHALL）。プラットフォームの自動タスク再試行はエージェントの二重実行を防げる設定としなければならない（SHALL）。

#### Scenario: Job設定を更新する
- **WHEN** 利用者が検証済みリリース設定でデプロイする
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
