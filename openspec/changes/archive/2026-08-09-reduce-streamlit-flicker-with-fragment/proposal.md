## Why

Streamlit サンプルは Agent の実行中に数秒間隔でアプリ全体を再実行するため、会話だけでなくサイドバーや入力領域まで繰り返し再描画され、画面のちらつきが目立つ。実行中に変化する領域だけを更新し、操作中の表示を安定させる必要がある。

## What Changes

- active Run の定期更新を Streamlit fragment 内で行い、実行状態、会話イベント、質問、タスク進捗、キャンセル操作など実行中に変化する領域だけを再描画する。
- 定期更新では Session、Run、最新イベントの revision だけを先に確認し、変更がない場合は履歴・interaction の再取得と動的領域の再構築を省略する。
- ユーザーが送信したプロンプトと既存のユーザーメッセージは fragment 外の固定領域に表示し、agent の応答・ツール・進捗・質問・タスクだけを動的領域で更新する。
- 定期更新中はセッション一覧など fragment 外の領域を再描画せず、全アプリの周期的な rerun を廃止する。
- Run が finish イベントまたは終端状態へ到達したときはアプリ全体を一度再実行し、サイドバーのセッション名・更新日時・選択状態を含む画面全体を最新状態へ同期する。
- 質問への回答中は既存どおり自動更新を停止し、入力ウィジェットの操作を妨げない。
- fragment の更新境界、終端遷移、質問待ちを検証する Streamlit フロントエンドのテストを追加する。

## Capabilities

### New Capabilities

なし。

### Modified Capabilities

- `realtime-chat-sample`: Streamlit の実行中更新を部分描画に限定し、Run 終端時だけ全画面を同期する表示要件を追加する。

## Impact

- `example/streamlit_frontend/app.py` の画面構成、自動更新、Run 終端検知が影響を受ける。
- `tests/test_sample_frontend.py` に fragment 更新と全画面同期の回帰テストを追加する。
- Streamlit 1.59.2 の fragment API を利用する。公開ライブラリ API、永続データ形式、Cloud Run Job、Slack Bot の動作には変更を加えない。
