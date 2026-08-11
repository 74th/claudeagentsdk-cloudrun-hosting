## Why

エージェントの利用者別・実行別の費用と処理時間を、アプリケーションが任意の分析基盤へ記録できる公開境界がない。将来 BigQuery などへ送信できるよう、SDK の利用実績を構造化した hook として受け取れるようにする。

## What Changes

- run の終端時に、ユーザー名、run ID、セッション名、Claude Agent SDK の推定料金、記録時刻、SDK 処理時間をまとめた利用実績をアプリケーション定義の Python hook へ渡す。
- 利用実績 hook を Agent runtime の起動 API から任意指定できるようにし、hook 未指定時の既存動作を維持する。
- SDK が料金または処理時間を返さない場合に、未取得値を 0 と誤認させないデータ契約を定義する。
- 利用実績の送信失敗が、既に確定した run の成否や終了コードを変更しないようにする。
- `example/agent` に利用実績を構造化ログへ出力する Python 関数と、その hook を runtime へ登録する例を追加する。

## Capabilities

### New Capabilities

- `agent-usage-reporting`: run の利用実績データ契約、アプリケーション定義 hook の呼び出し、およびサンプルのログ出力を規定する。

### Modified Capabilities

- なし。

## Impact

- `cas_hosting_adapter` の公開 runtime 型、Google Cloud Job composition、および Claude Agent 実行境界に、利用実績レコードと任意 hook を追加する。
- `example/agent` の composition root にログ出力 hook を追加する。
- hook のデータ契約・呼び出し条件・失敗時挙動とサンプル出力を検証するテストを追加する。
- BigQuery クライアントや新しい外部依存は追加せず、外部送信先の実装は hook 利用者に委ねる。
