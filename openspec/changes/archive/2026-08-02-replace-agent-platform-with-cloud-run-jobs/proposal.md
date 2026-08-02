## Why

Gemini Enterprise Agent Platform の BYOC では、長時間実行を開始しても Operation が `RUNNING` のままコンテナの REST API へ到達しない事象があり、現行ドキュメントだけでは信頼できる長時間実行経路を確立できない。実行基盤を Cloud Run Jobs、会話・実行状態・リアルタイムイベントの正本を Firestore へ移し、将来ほかの実行基盤やストレージにも交換できるホスティング構成へ改める。

## What Changes

- **BREAKING** Agent Platform の runtime API、Sessions、Long-running Operations を前提とするホスティング方式を廃止し、1 run を独立したバックグラウンドジョブとして起動する方式へ変更する。
- 最初の実行バックエンドとして Cloud Run Jobs を実装し、開始、状態確認、キャンセル、実行識別子の取得を共通インターフェイス越しに提供する。
- 将来 Kubernetes Job などへ交換できるよう、ジョブ実行をプロバイダー非依存の契約とし、プロバイダー固有の状態・識別子・エラーを正規化する。
- Firestore をセッション、run、ユーザーメッセージ、エージェント応答、進捗、キャンセル要求、終端状態の正本として使用する。
- Firestore のリアルタイムリスナーを利用し、チャット UI が実行中の応答と進捗を増分取得できるようにする。再接続時は永続化済みイベントの続きから表示を再開する。
- Firestore からユーザー別のセッション一覧を更新日時順かつページング付きで取得し、過去の会話と active run を再訪できるようにする。
- Firestore への依存をセッション・run・イベント用のストア契約の背後へ隔離し、別データベースまたはイベントストアへ交換可能にする。
- Claude Agent SDK の transcript とセッション別ワークスペースは引き続きオブジェクトストレージへ不変 snapshot として保存するが、GCS への依存をワークスペースストア契約の背後へ隔離する。
- Firestore トランザクションで 1 セッションにつき active run を最大 1 件に制御し、ジョブの二重起動、再試行、部分失敗を冪等に処理する。
- チャット UI／制御クライアント、ジョブコンテナ、永続ストア、実行バックエンドを分離し、ジョブコンテナは run ID を起点に入力取得、状態復元、エージェント実行、イベント追記、snapshot commit を完結する。
- Cloud Run Jobs、Firestore、GCS、Artifact Registry、サービスアカウント、最小権限 IAM、必要な Firestore index を Terraform で構築する。
- 既存実装のうち Claude Agent SDK adapter、イベントモデル、snapshot の安全性、再試行、ログなど再利用可能な部分を移行し、Agent Platform 固有コードと前提を除去する。
- Docker イメージ、Claude Agent SDK エージェント、Streamlit チャット UI の実行可能なサンプルと、外部サービスを差し替えたテストを更新する。

## Capabilities

### New Capabilities

- `job-execution-backend`: 長時間 run をジョブとして開始・照会・キャンセルし、Cloud Run Jobs を最初の実装としながら別実行基盤へ交換できる機能。
- `firestore-chat-store`: ユーザー別セッション、run、順序付きチャットイベント、状態を Firestore に永続化し、リアルタイム購読とセッション一覧を提供する機能。
- `agent-job-lifecycle`: 実行要求から状態復元、Claude Agent SDK 実行、イベント配信、snapshot commit、失敗・キャンセル時の後処理までを冪等に制御する機能。
- `workspace-object-store`: Claude transcript とセッション別ワークスペースを不変 snapshot として保存・復元し、GCS を別オブジェクトストレージへ交換可能にする機能。
- `cloud-run-job-deployment`: Cloud Run Jobs、Firestore、GCS、Artifact Registry、IAM と必要な設定を Terraform およびリリース設定で構築・配備する機能。
- `realtime-chat-sample`: Streamlit からセッションの作成・一覧・再訪、run の開始・リアルタイム表示・キャンセルを確認できるサンプル機能。

### Modified Capabilities

なし。

## Impact

- 変更対象: `cas_hosting_adapter/` の公開モデル、Protocol、client、lifecycle、Google Cloud adapter、`example/`、`sample_frontend/`、`terraform/`、デプロイスクリプト、テスト、README。
- 公開 API: Agent Platform runtime server 契約を廃止し、ジョブ実行バックエンド、チャットストア、ワークスペースストア、制御クライアント、ジョブエントリーポイントの契約へ置換する。
- 主な依存先: Claude Agent SDK、Google Cloud Run Jobs、Cloud Firestore、Google Cloud Storage、Artifact Registry、Streamlit、Terraform Google Provider。
- 外部リソース: Cloud Run Job と Execution、Firestore database・collections・indexes、GCS bucket、Artifact Registry、制御主体とジョブ実行サービスアカウント、および対象リソースへ限定した IAM。
- 移行元: `add-gemini-enterprise-hosting-adapter` で完了済みの汎用実装を選別して再利用し、Agent Platform 固有の Sessions、Operations、runtime API、Gateway 設計は移行対象外とする。
