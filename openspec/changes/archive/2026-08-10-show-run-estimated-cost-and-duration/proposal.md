## Why

現在は1回のrunが完了しても、Claude Agent SDKが返す推定費用と処理時間が保存・表示されないため、利用者は処理単位のコスト感と所要時間を確認できない。SDKが既に提供する集計値を永続イベントとして扱い、StreamlitとSlack Botのどちらからも同じ情報を確認できるようにする。

## What Changes

- Claude Agent SDKの各ResultMessageから、利用可能な推定総費用（USD）とSDK処理時間を取得する。
- 推定総費用と処理時間をrunの終端イベントへ保存し、再接続や過去セッションの再訪後も復元できるようにする。
- Streamlitの各処理結果に、推定値であることと単位を明示して費用と処理時間を表示する。
- Slack Botの同一スレッドに投稿する最終結果へ、同じ推定費用と処理時間を表示する。
- SDKが値を返さない終了経路では0として補完せず、取得できた項目だけを表示する。
- Web Searchの費用は独自の検索単価表では再計算せず、Claude Agent SDKの推定総費用に含まれる範囲で取り込む。

## Capabilities

### New Capabilities

なし。

### Modified Capabilities

- `agent-job-lifecycle`: SDKの推定費用と処理時間を処理単位の終端イベントとして永続化する要件を追加する。
- `streaming-chat-client`: 共通イベント表現で処理メタデータを欠損・未知フィールドへ耐性を持って配信する要件を追加する。
- `realtime-chat-sample`: StreamlitとSlack Botの両方で処理単位の推定費用と処理時間を表示する要件を追加する。

## Impact

- `cas_hosting_adapter/agent_adapter.py` のResultMessage正規化と、そのイベントを保存するジョブライフサイクルに影響する。
- `example/chat/events.py` の共通イベント解釈、`example/streamlit_frontend/app.py` と `example/slackbot_frontend/handler.py` の最終結果表示に影響する。
- イベントpayloadへ後方互換な任意フィールドを追加する。既存の永続イベント、Runモデル、外部SDK依存関係には破壊的変更を加えない。
- 推定費用はSDK同梱の価格情報による参考値であり、Google Cloud Billingの正式な請求額を置き換えない。
