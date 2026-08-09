## ADDED Requirements

### Requirement: Run IDから外部Executionの終端を定期検知する
実行バックエンドは、永続化済みRunの実行参照をRun IDから解決して状態を再照会でき、Cloud Run Executionが停止・失敗・キャンセル・消失したことを制御側が検知できる契約を提供しなければならない（SHALL）。pendingまたはrunningの実行は終端扱いにしてはならず、状態取得の一時的な失敗だけでRunを異常終了にしてはならない（MUST NOT）。

#### Scenario: 実行中のRunを再照会する
- **WHEN** 制御側がactiveなRun IDのExecution状態を再照会する
- **THEN** バックエンドは保存済みのExecution参照を使い、pending、running、succeeded、failed、cancelledのいずれかへ正規化した状態を返す

#### Scenario: Cloud Run Executionが失敗して停止する
- **WHEN** 再照会したCloud Run Executionが完了条件の失敗または停止を示す
- **THEN** バックエンドはfailedまたはcancelledとして区別できる状態と、制御側が保存できる安全な理由を返す

#### Scenario: Executionが見つからない
- **WHEN** 既に永続化されたExecution参照を再照会したが対象Executionが存在しない
- **THEN** バックエンドは実行消失として識別可能なエラーを返し、Runをpendingまたはrunningのまま成功扱いにしない

#### Scenario: Cloud Run APIが一時的に利用できない
- **WHEN** 状態再照会が一時的なネットワーク、quota、またはサービス利用不能エラーになる
- **THEN** バックエンドは再試行可能なエラーを返し、制御側はRunの終端状態を変更しない

### Requirement: 終端Executionの再照会を冪等に扱う
実行バックエンドの状態再照会は同じRun IDに対して複数回、または複数の制御プロセスから行われても同じ終端結果を返し、既に終端となったRunを再び実行中へ戻してはならない（MUST NOT）。

#### Scenario: 失敗検知を再試行する
- **WHEN** 同じfailedまたはcancelled ExecutionをRun IDで繰り返し再照会する
- **THEN** バックエンドは同じ終端状態を返し、重複Executionや重複エラーイベントを作成しない
