# エージェントとフロントエンドのカスタマイズ

サンプルは動作確認だけでなく、用途に合わせたエージェントとフロントエンドを作る際の出発点として分割されています。

```text
example/
├── agent/                  Job で動くエージェント本体
├── chat/                   フロントエンド共通の会話サービス
├── streamlit_frontend/     Web UI の実装例
├── slackbot_frontend/      Slack Socket Mode の実装例
├── cli.py                  JSON Lines クライアント
└── Dockerfile              Job image
```

## エージェント本体を変更する

`example/agent/runtime.py` の `ClaudeAgentConfig` が、エージェント固有の主な変更点です。

- `system_prompt`: エージェントの役割と振る舞い
- `model`: Vertex AI で利用する Claude model
- `allowed_tools`: エージェントに許可する tool
- `setup_workspace()`: 各 run の開始時に必要なファイルやディレクトリを冪等に用意する処理
- `usage_hook`: run ごとの利用情報を社内の計測基盤へ送る処理

Job の起動 entrypoint は `python -m example.agent` です。変更後は `example/Dockerfile` を基に image を作成し、release config の `image` を新しい digest へ更新します。

framework の `ClaudeAgentAdapter.run_job()` が、owner claim、入力の取得、snapshot の復元、SDK event の永続化、transcript の resume、質問 timeout、終端処理、temporary directory の cleanup を担当します。通常のエージェント開発では、この lifecycle を複製せず `ClaudeAgentConfig` と workspace hook を変更します。

## 用途に合わせたフロントエンドを作る

`example.chat.ChatService` は UI framework に依存しない共通層です。独自 UI では、認証済みの利用者を安定した user ID に対応付けた上で、主に次を呼び出します。

- 新しい session または既存 session で run を開始する
- session と run の履歴を一覧する
- 保存済み event を読み、cursor 以降を購読する
- active run を reconcile する
- run をキャンセルする
- エージェントからの質問へ回答する

`ControlClient` を直接利用する場合は、user/session/run の所有境界、event の catch-up と重複排除、再接続をフロントエンド側で実装します。これらをまとめて利用したい場合は `ChatService` を組み込んでください。

フロントエンドはこのリポジトリの Terraform には含まれません。Cloud Run service、GKE、社内サーバーなどへ別途配備し、次を準備します。

- release config と同等の非秘密設定
- Firestore と選択した実行バックエンドへの IAM
- GKE を使う場合は Kubernetes API への到達性と RBAC
- 利用者認証と user ID の決定方法
- active run を画面外でも監視する場合の定期 reconciler

## Streamlit サンプル

```bash
gcloud auth application-default login
uv sync --group streamlit
uv run streamlit run example/streamlit_frontend/app.py
```

サイドバーで release config と User ID を指定します。会話の開始、イベントの増分表示、再訪、質問への回答、キャンセルの実装例として利用できます。本番では自由入力の User ID を使わず、認証済み identity から決定してください。

## Slack サンプル

Slack App で Socket Mode を有効にし、Bot Token Scopes に `chat:write` と `channels:history` を付与します。private channel を使う場合は `groups:history` も必要です。Event Subscriptions で `message.channels` または `message.groups` を購読し、Bot を対象 channel へ招待します。

秘密値は release config へ書かず、フロントエンドの secret manager から環境変数として渡します。

```bash
export SLACK_APP_TOKEN=xapp-...
export SLACK_BOT_TOKEN=xoxb-...
export SLACK_SIGNING_SECRET=...
export SLACK_BOT_USER_ID=U...
export SLACK_TEAM_ID=T...
uv sync --group slack
uv run python -m example.slackbot_frontend.app \
  --release-config release.production.yaml
```

team/channel/thread と user/session の対応は Firestore に保存されるため、Bot の再起動後も同じ thread の会話を継続できます。Slack event ID を idempotency key に利用し、再配送による二重実行を防ぎます。

## CLI サンプル

CLI は自動化や疎通確認に利用でき、標準出力へ JSON Lines を出力します。

```bash
uv run python -m example.cli \
  --release-config release.production.yaml \
  --user-id test-user \
  --prompt 'リポジトリの概要を説明して' \
  --idempotency-key cli-request-1
```

継続会話では、開始結果の `session_id` を次の実行へ渡します。

```bash
uv run python -m example.cli \
  --release-config release.production.yaml \
  --user-id test-user \
  --session-id <SESSION_ID> \
  --prompt '前の回答を短くまとめて'
```

release config には秘密値を保存しないでください。設定エラーや開始失敗は終了コード 2、run の失敗・キャンセル・timeout は終了コード 1 です。
