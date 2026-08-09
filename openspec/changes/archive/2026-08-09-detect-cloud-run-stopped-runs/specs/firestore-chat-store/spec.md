## ADDED Requirements

### Requirement: 外部Executionの終端状態をRunへ原子的に反映する
Firestore実装は、Run IDで所有Runを特定した制御側から外部Executionの終端状態を受け取ったとき、Runの終端状態、終了時刻、安全なエラーコード、およびセッションのactive run参照を一貫して更新できなければならない（SHALL）。既に終端状態のRunを再照会で復活させてはならず、同じ終端反映を複数回適用しても結果を重複させてはならない（MUST NOT）。

#### Scenario: Cloud Runの失敗をRunへ反映する
- **WHEN** 制御側がRun IDに対応するExecutionの失敗と安全なエラーコードを反映する
- **THEN** FirestoreはRunをfailed、終了時刻、エラーコードへ更新し、セッションのactive run参照を解除して後続Runを開始できるようにする

#### Scenario: Cloud RunのキャンセルをRunへ反映する
- **WHEN** 制御側がExecutionのキャンセル完了を反映する
- **THEN** FirestoreはRunをcancelledとして保存し、セッションの最新Run状態を更新する

#### Scenario: 終端Runを再照会する
- **WHEN** failed、cancelled、completedのいずれかに確定したRunへ同じまたは異なる再照会結果を反映しようとする
- **THEN** Firestoreは既存の終端状態を実行中へ戻さず、所有権と状態遷移の整合性を維持する

#### Scenario: 複数のreconcilerが同時に更新する
- **WHEN** 複数の制御プロセスが同じRun IDの終端反映を同時に要求する
- **THEN** Firestoreはreconciliation leaseまたは同等の排他境界で1つの更新だけを有効にし、active run解除と保持期限更新を壊さない
