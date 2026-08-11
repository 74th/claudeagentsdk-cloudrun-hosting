## 1. 利用実績の公開契約

- [x] 1.1 provider 非依存の runtime module に不変な `AgentUsageRecord` と同期 `UsageHook` 型を追加し、必須識別情報、UTC 終端時刻、任意の SDK 料金・処理時間を表現する
- [x] 1.2 利用実績型を package root から公開し、型の値保持と未取得値の表現を単体テストする

## 2. runtime hook の組み込み

- [x] 2.1 Google Cloud Job composition と Claude Agent 実行境界へ、後方互換な keyword-only の任意 `usage_hook` を引き渡す
- [x] 2.2 terminal commit 後に、確定 run、session 表示名、永続化済み SDK 終端イベントから利用実績を組み立て、登録された hook を 1 回呼び出す
- [x] 2.3 hook の例外を run ID 付きでログへ隔離し、確定済み状態と終了コードを維持する

## 3. サンプル Agent

- [x] 3.1 `example/agent` に全利用実績項目を JSON 互換の単一レコードとして INFO ログへ出力する Python hook 関数を追加する
- [x] 3.2 サンプルの composition root でログ hook を登録し、UUID・UTC 時刻の文字列化と未取得値の `null` 出力をテストする

## 4. lifecycle 検証

- [x] 4.1 正常・失敗・cancel・timeout の終端経路で、ユーザー名、run ID、セッション名、SDK 料金、UTC 終端時刻、SDK 処理時間が期待どおり通知されることをテストする
- [x] 4.2 hook 未指定、所有権取得のスキップ、SDK 利用量欠落、および hook 例外の各経路で、呼び出し回数と既存の run 状態・終了コードが維持されることをテストする
- [x] 4.3 関連するテストスイートと OpenSpec strict validation を実行し、公開 API と仕様の整合を確認する
