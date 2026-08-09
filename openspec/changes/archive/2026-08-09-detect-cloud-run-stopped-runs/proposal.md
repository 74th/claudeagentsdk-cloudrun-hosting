## Why

Cloud Run Jobが失敗または停止しても、Job側がFirestoreへ終端状態を書き込めない場合、Runが実行中のまま残り、利用者はRun IDとFirestoreを個別に確認しなければならない。制御側がRun IDに紐づくCloud Run Executionを定期的に確認し、停止・失敗を検知してRunとUIへ反映できるようにする。

## What Changes

- Run IDから保存済みのCloud Run Execution参照を取得し、実行状態を定期的に再照会するreconciliation経路を整備する。
- Cloud Run Executionの失敗、キャンセル、消失、完了後の永続化不整合を、Runの終端状態と安全なエラー情報へ反映する。
- active runを表示しているStreamlit UIから、既存の更新サイクルに合わせてreconciliationを実行する。
- 検知済みの終端Runを再度実行中へ戻さず、同じRun IDの再照会や複数のreconcilerが重複更新しても冪等にする。
- 一時的なCloud Run API障害ではRunを誤って失敗扱いにせず、次回の定期照会へ持ち越す。

## Capabilities

### New Capabilities

なし。

### Modified Capabilities

- `job-execution-backend`: Run IDに紐づくCloud Run Executionの定期状態照会、停止・失敗・消失の検知、および再試行可能な状態取得エラーの扱いを追加する。
- `firestore-chat-store`: 外部Executionの状態をRunへ原子的かつ冪等に反映し、active run参照を解除する契約を明確化する。
- `realtime-chat-sample`: active runの定期更新時にreconciliationを実行し、Cloud Run Jobの異常終了をUIへ表示する。

## Impact

`ControlClient`のreconciliation API、`ExecutionBackend`とCloud Run状態マッピング、ChatStoreの終端更新とエラーコード、Streamlitのactive run更新ループ、および関連テストが影響を受ける。Cloud Schedulerなど新しい常駐基盤は必須にせず、既存の制御クライアントを外部ポーラーからも呼び出せる契約を維持する。
