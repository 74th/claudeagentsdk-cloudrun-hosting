## Why

現在のデプロイはプロジェクトの `(default)` Firestore database を固定で使用しているため、同一プロジェクト内の複数プロダクトのデータとライフサイクルを分離しにくい。サンプルを専用の名前付き database `claude-agent-chat` へ配置し、意図しない共有を防ぐ。

## What Changes

- Terraform が `(default)` ではなく `claude-agent-chat` という Firestore Native database と、その database に属する必要な index を作成する。
- リリース設定にFirestore database名を追加し、Terraform、Streamlit制御クライアント、およびCloud Run Jobが同じ名前付き database を使用する。
- 設定のdatabase名を検証し、未指定時に暗黙に `(default)` へ接続しない。
- サンプル設定と利用ドキュメントを `claude-agent-chat` に更新する。

## Capabilities

### New Capabilities

なし。

### Modified Capabilities

- `cloud-run-job-deployment`: Firestore database名をリリース設定から一貫して配備・利用し、`(default)` databaseを利用しないようにする。
- `realtime-chat-sample`: サンプルUIとJobがリリース設定で選択した名前付きFirestore databaseへ接続するようにする。

## Impact

- `terraform/main.tf`、Terraform変数、Cloud Run Jobの環境変数、Firestore index定義
- `cas_hosting_adapter/release_config.py` とリリース設定を読むUI/デプロイ処理
- `sample_frontend/app.py`、`example/agent.py`、関連する単体・結合テスト、README
- 新規環境では `claude-agent-chat` が作成される。既存の `(default)` database のデータは移行しない。
