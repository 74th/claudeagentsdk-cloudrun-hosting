## ADDED Requirements

### Requirement: active Runの外部異常終了を定期表示する
Streamlitサンプルはactive Runを表示している間、Run IDに紐づく外部Executionの状態を既存の定期更新サイクルで再照会し、Cloud Run Jobの失敗、停止、キャンセル、実行消失をFirestoreのRun状態へ反映して画面に表示しなければならない（SHALL）。pendingまたはrunningの間は実行中表示を継続し、再照会の一時的な失敗だけで異常終了と表示してはならない（MUST NOT）。

#### Scenario: Cloud Run Jobが失敗して停止する
- **WHEN** active RunのCloud Run Executionが失敗して停止し、次の定期更新が実行される
- **THEN** UIはRunを実行中のまま表示し続けず、失敗状態と安全なエラー情報を表示して新しいRunを開始できる状態へ戻る

#### Scenario: Executionがまだ実行中である
- **WHEN** 定期更新時点でExecutionがpendingまたはrunningである
- **THEN** UIは実行中表示を継続し、Runをfailedまたはcancelledへ変更しない

#### Scenario: 状態再照会が一時的に失敗する
- **WHEN** Cloud Run APIの状態再照会が一時的なエラーになる
- **THEN** UIはRunを直ちに異常終了とせず、次の更新サイクルで再試行する

#### Scenario: 失敗表示後に画面を再訪する
- **WHEN** 利用者が失敗検知後に同じセッションを再訪する
- **THEN** UIはFirestoreに保存された失敗状態、Run ID、Cloud Run execution ID、および安全なエラー情報を復元して表示する
