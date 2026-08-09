## Why

現在のジョブ開始、イベント取得、履歴整形の処理は Streamlit サンプルに寄っており、Slack Bot など別のフロントエンドから同じ会話実行基盤を再利用しにくい。フロントエンド非依存のモジュールと CLI を先に確立し、ストリーミング応答を単独で検証できるようにしたうえで、Streamlit と Slack Bot の双方を薄いアダプターとして提供する。

## What Changes

- セッション開始、run 開始、保存済みイベントの取得、増分イベントの購読、終端状態の判定をフロントエンド非依存のモジュールへ切り出す。
- 共通モジュールを呼び出し、入力したプロンプトへの応答イベントを標準出力へ逐次表示する CLI サンプルを追加する。
- 既存サンプルを `example/agent` と `example/streamlit_frontend` へ再編し、Streamlit 固有コードから共通処理を除去する。
- Slack のメッセージを共通モジュールへ渡し、応答を同じスレッドへ段階的に反映する `example/slackbot_frontend` を追加する。
- 各サンプルの起動方法、設定、ストリーミング確認方法をドキュメント化する。
- **BREAKING**: 既存の `example/agent.py` と `sample_frontend` の import path および起動パスを、新しい `example/*` 配下の構成へ変更する。

## Capabilities

### New Capabilities

- `streaming-chat-client`: UI に依存せず会話 run を開始してイベントをストリーミング取得できる共通モジュールと、その動作を確認する CLI の契約。

### Modified Capabilities

- `realtime-chat-sample`: Streamlit サンプルを共通モジュールの利用側として再編し、同じ会話機能を利用する Slack Bot サンプルと利用手順を追加する。

## Impact

- `sample_frontend`、`example/agent.py`、関連テストおよびパッケージ探索設定を `example/agent`、`example/streamlit_frontend`、`example/slackbot_frontend` 中心の構成へ移行する。
- `ControlClient` を利用するフロントエンド非依存モジュールと CLI エントリーポイントを追加する。
- Slack Bot SDK の依存関係、Slack App の認証情報およびイベント購読設定を追加する。
- README、サンプルの実行手順、既存 import path を参照するテストやデプロイ設定を更新する。
