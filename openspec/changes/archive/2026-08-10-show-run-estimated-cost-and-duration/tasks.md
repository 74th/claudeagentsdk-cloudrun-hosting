## 1. SDK結果の永続化

- [x] 1.1 Claude Agent adapterでResultMessageの`total_cost_usd`と`duration_ms`を検証し、取得できた値を`estimated_cost_usd`と`duration_ms`としてfinal/errorイベントpayloadへ追加する
- [x] 1.2 Job runnerの正常完了がSDK由来finalイベントの処理メタデータを保持し、成功・エラー・欠損・無効値・Web Search込み推定総額を想定したadapter/runnerテストを追加する

## 2. 共通イベントの処理メタデータ

- [x] 2.1 共通チャットイベント層へ、推定費用と処理時間を有限・非負・非boolの任意値として解釈する共通値表現を追加する
- [x] 2.2 USD小数6桁とSDK処理秒数小数2桁の共通表示整形を実装し、片側欠損、両方欠損、旧イベント、未知・無効値のテストを追加する

## 3. フロントエンド表示

- [x] 3.1 Streamlitのfinal/errorイベント表示へ処理メタデータを対応付け、ライブ更新と過去セッション再訪の両方で表示されるテストを追加する
- [x] 3.2 Slack Botでライブストリームと履歴フォールバックから処理メタデータを取得し、成功時の最終結果または失敗時の終端状態へ付記するテストを追加する
- [x] 3.3 処理メタデータが片側または両方欠ける既存イベントで、StreamlitとSlack Botが0を補完せず従来表示を継続することを確認する

## 4. 検証と文書化

- [x] 4.1 READMEへ表示項目、SDK推定値であること、Web Search費用はSDK推定総額に含まれる範囲で扱うこと、および正式請求はGoogle Cloud Billingを参照することを追記する
- [x] 4.2 adapter、Job runner、共通チャット層、Streamlit、Slack Botの関連テストを実行して回帰がないことを確認する
- [x] 4.3 プロジェクトの静的解析・フォーマット検査と全テストスイートを実行する
