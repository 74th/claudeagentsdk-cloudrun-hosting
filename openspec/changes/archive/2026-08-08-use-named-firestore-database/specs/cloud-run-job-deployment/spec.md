## MODIFIED Requirements

### Requirement: バージョン付きリリース設定を検証する
システムは project、region、container image、Cloud Run Job、Firestore database名、GCS、サービスアカウント、実行制限を version 付き設定で管理し、未知の項目、不正な組み合わせ、未対応 schema version をクラウド変更前に拒否しなければならない（SHALL）。Firestore database名は空文字列および `(default)` を許可してはならない（MUST NOT）。

#### Scenario: 不正な設定を読み込む
- **WHEN** リリース設定に未知の項目、region の不整合、空のFirestore database名、または `(default)` がある
- **THEN** デプロイ処理は対象項目を示して失敗し、クラウドリソースを変更しない

#### Scenario: 名前付きFirestore databaseを設定する
- **WHEN** 利用者が有効な名前付きFirestore database名を含むリリース設定を読み込む
- **THEN** そのdatabase名はTerraform入力とCloud Run Jobの非秘密環境設定へ同じ値で渡される

### Requirement: Firestoreのクエリ要件を配備する
デプロイ構成はリリース設定で指定された名前付きFirestore Native databaseの mode、location、index を明示し、アプリケーション設定との不一致を拒否しなければならない（SHALL）。デプロイ構成は `(default)` databaseを作成または参照してはならない（MUST NOT）。

#### Scenario: セッション一覧用indexを計画する
- **WHEN** 有効な名前付きFirestore databaseを指定してTerraform plan を作成する
- **THEN** セッションを更新日時順にページングするために必要な index が、その指定databaseを対象として計画へ含まれる

#### Scenario: サンプルdatabaseを新規環境へ適用する
- **WHEN** `claude-agent-chat` を指定した有効な環境設定で Terraform を適用する
- **THEN** Firestore Native database `claude-agent-chat` と必要な index が作成され、`(default)` databaseはこの構成によって作成または変更されない
