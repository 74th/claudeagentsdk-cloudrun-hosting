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
システムは project、region、container image、Cloud Run Job、Firestore、GCS、サービスアカウント、実行制限を version 付き設定で管理し、未知の項目、不正な組み合わせ、未対応 schema version をクラウド変更前に拒否しなければならない（SHALL）。

#### Scenario: 不正な設定を読み込む
- **WHEN** リリース設定に未知の項目または region の不整合がある
- **THEN** デプロイ処理は対象項目を示して失敗し、クラウドリソースを変更しない

### Requirement: Cloud Run Jobへ実行設定を適用する
デプロイ処理はコンテナイメージ、CPU、メモリ、task timeout、再試行回数、同時タスク数、実行サービスアカウント、必要な非秘密環境設定を Cloud Run Job へ適用しなければならない（SHALL）。プラットフォームの自動タスク再試行はエージェントの二重実行を防げる設定としなければならない（SHALL）。

#### Scenario: Job設定を更新する
- **WHEN** 利用者が検証済みリリース設定でデプロイする
- **THEN** Cloud Run Job は指定された実行制限とサービスアカウントを持つ revision へ更新される

### Requirement: Firestoreのクエリ要件を配備する
デプロイ構成はユーザー別セッション一覧と run イベント購読に必要な database mode、location、index を明示し、アプリケーション設定との不一致を拒否しなければならない（SHALL）。

#### Scenario: セッション一覧用indexを計画する
- **WHEN** Terraform plan を作成する
- **THEN** セッションを更新日時順にページングするために必要な index が計画へ含まれる

### Requirement: 保存データの保持期限を構成する
デプロイ構成は snapshot と一時オブジェクトの lifecycle、および run・イベントの保持方針を設定可能にし、既定値を配備前に表示しなければならない（SHALL）。

#### Scenario: 既定保持期間で計画する
- **WHEN** 利用者が保持期間を省略する
- **THEN** システムは採用した既定値と削除対象を plan 前に表示する

### Requirement: 秘密情報を成果物へ埋め込まない
リリース設定、Terraform 変数、コンテナイメージ、ログへ API key、サービスアカウント鍵、アクセストークンを埋め込んではならない（MUST NOT）。

#### Scenario: 秘密情報らしい設定を指定する
- **WHEN** 利用者が禁止された秘密情報項目をリリース設定へ記述する
- **THEN** 検証はクラウド変更前に設定を拒否する

