## Why

現在のサンプルはエージェントの本文や一般的なツール実行を表示できる一方、`AskUserQuestion` で利用者の判断を待つことも、Task 系ツールが表す計画と進捗を分かりやすく表示することもできない。長時間 run を Streamlit と Slack のどちらからでも対話的に継続し、作業状況を同じ意味で把握できるようにする必要がある。

## What Changes

- Claude Agent SDK の `AskUserQuestion` を検出し、質問、2〜4 個の選択肢、複数選択可否を永続イベントとして配信して、回答到着まで run を安全に待機・再開できるようにする。
- 質問回答を所有者と対象 run に結び付けて冪等に保存・取得する制御契約を追加し、古い質問、重複回答、終端済み run への回答を拒否する。
- `TaskCreate`、`TaskUpdate`、`TaskGet`、`TaskList` の tool use / tool result から、タスク ID、内容、状態、依存関係を持つ共通タスク状態を復元する。
- Streamlit に選択肢、複数選択、「その他」の自由入力、送信待ち表示、および最新タスクリストを表示する。
- Slack に番号付き選択肢と自由入力方法を投稿し、同じスレッドの `1`、`1,3`、または任意テキストを pending 質問への回答として扱う。通常の後続プロンプトとは明確に区別する。
- Streamlit と Slack の再接続・プロセス再起動後も、未回答質問とタスク進捗を永続履歴から復元する。
- 対話質問、タスク進捗、競合、再接続、入力検証を対象とする単体・統合テストと利用手順を追加する。

## Capabilities

### New Capabilities

なし。

### Modified Capabilities

- `agent-job-lifecycle`: SDK の質問コールバックを永続的な回答待ちへ接続し、Task 系ツールの結果を含むイベントを逐次保存する。
- `firestore-chat-store`: run ごとの pending 質問と回答を所有者境界、冪等性、保持期間を守って永続化・通知する。
- `streaming-chat-client`: 質問・回答とタスク状態の共通表現、および active run へ回答を送るフロントエンド非依存 API を提供する。
- `realtime-chat-sample`: Streamlit と Slack Bot の双方で選択式質問への回答とタスク進捗表示を提供する。

## Impact

- `cas_hosting_adapter` のモデル、ストアポート、Firestore／インメモリ実装、制御クライアント、Agent SDK アダプター、ジョブ実行ライフサイクルが影響を受ける。
- `example/chat` の共通イベント・状態・サービス API、`example/streamlit_frontend`、`example/slackbot_frontend`、関連テストと README が影響を受ける。
- Firestore には run 配下の対話状態または同等の永続レコードと索引・TTL 設定が追加される可能性がある。
- 既存の run 開始、イベント購読、キャンセル API との後方互換性を維持し、既存イベント種別の意味は変更しない。
