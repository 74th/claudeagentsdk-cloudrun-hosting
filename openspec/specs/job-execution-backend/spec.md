# job-execution-backend Specification

## Purpose

長時間のエージェント実行を接続から分離したジョブとして管理し、最初の Cloud Run Jobs 実装を将来ほかの実行基盤へ交換できる共通契約を提供する。

## Requirements

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

### Requirement: 実行バックエンドを共通契約で操作する
システムは run の開始、状態取得、キャンセルをプロバイダー非依存の契約として提供し、バックエンド固有の識別子と状態を共通モデルへ正規化しなければならない（SHALL）。

#### Scenario: runを開始する
- **WHEN** 制御クライアントが永続化済みの run ID を指定して実行を開始する
- **THEN** システムはバックエンド実行を作成し、run ID と関連付けた実行参照を返す

#### Scenario: 実行状態を取得する
- **WHEN** 制御クライアントが実行参照の状態を取得する
- **THEN** システムは `pending`、`running`、`succeeded`、`failed`、`cancelled` のいずれかへ正規化した状態を返す

### Requirement: Cloud Run Jobsでrunを実行する
最初の実行バックエンドは 1 run を Cloud Run Job の独立した Execution として開始し、ジョブコンテナへ run ID と必要最小限の非秘密設定だけを渡さなければならない（SHALL）。入力メッセージや認証情報をコマンド引数または環境変数へ埋め込んではならない（MUST NOT）。

#### Scenario: Cloud Run Executionを作成する
- **WHEN** 有効な run の開始を Cloud Run Jobs バックエンドへ要求する
- **THEN** バックエンドは設定済み Job から Execution を作成し、コンテナが run ID を使って永続ストアから入力を取得できるようにする

### Requirement: 重複ディスパッチからエージェント実行を保護する
システムは同じ run ID への開始要求またはバックエンドの再試行が複数回発生しても、同一 run のエージェント処理を同時に複数実行してはならない（MUST NOT）。

#### Scenario: 同じrunを再度開始する
- **WHEN** 既に実行参照または実行所有者を持つ run ID へ開始要求が再送される
- **THEN** システムは既存の実行参照を返すか重複 Execution を無処理で終了させ、新しいエージェント処理を開始しない

### Requirement: 実行を明示的にキャンセルする
システムは active run のキャンセル要求を実行バックエンドへ伝え、要求受付と実際の停止完了を区別しなければならない（SHALL）。既に終端状態の実行へのキャンセルは冪等に扱わなければならない（SHALL）。

#### Scenario: 実行中runをキャンセルする
- **WHEN** 利用者が実行中 run のキャンセルを要求する
- **THEN** システムはキャンセル要求済みを記録してバックエンドへ停止要求を送り、停止確認後に `cancelled` とする

#### Scenario: 完了済みrunをキャンセルする
- **WHEN** 利用者が終端状態の run へキャンセルを再送する
- **THEN** システムは元の終端状態を変更せず現在状態を返す

### Requirement: バックエンド障害を永続状態へ反映する
システムはディスパッチ失敗、実行消失、バックエンド失敗、状態取得不能を区別できるエラーとして返し、開始に失敗した run を実行中のまま放置してはならない（MUST NOT）。

#### Scenario: Execution作成に失敗する
- **WHEN** バックエンドが run の Execution を作成できない
- **THEN** システムは run をディスパッチ失敗として記録し、同じセッションで後続 run を安全に開始できる状態へ戻す

### Requirement: Google Cloud Batchでrunを実行する
Google Cloud Batch バックエンドは 1 run を一意な 1 Batch Job として開始し、ジョブコンテナへ run ID と必要最小限の非秘密設定だけを渡さなければならない（SHALL）。入力メッセージや認証情報をコマンド引数または環境変数へ埋め込んではならず（MUST NOT）、コンテナは既存の Firestore と GCS から run と workspace を取得しなければならない（SHALL）。

#### Scenario: Batch Jobを開始する
- **WHEN** 有効な run の開始を Google Cloud Batch バックエンドへ要求する
- **THEN** バックエンドは run ID から再現可能かつ衝突しない Job ID を持つ Batch Job を作成し、そのリソース名を実行参照として返す

#### Scenario: Batchコンテナへ入力を渡す
- **WHEN** 作成された Batch Job のコンテナが起動する
- **THEN** コンテナは渡された run ID を使って Firestore から入力を、GCS から workspace を取得し、Cloud Run 実行時と同じ JobRunner を実行する

### Requirement: Batch Jobの状態を共通状態へ正規化する
Google Cloud Batch バックエンドは Batch の queued、scheduled、running、成功、失敗、削除／キャンセル状態を、共通契約の `pending`、`running`、`succeeded`、`failed`、`cancelled` のいずれかへ正規化しなければならない（SHALL）。状態取得の一時的失敗と Job 消失は区別しなければならない（SHALL）。

#### Scenario: Batch Jobの状態を照会する
- **WHEN** 制御側が Batch Job の実行参照を照会する
- **THEN** バックエンドは Batch 固有状態を共通状態へ変換し、プロバイダー SDK オブジェクトを公開しない

#### Scenario: Batch Jobが見つからない
- **WHEN** 永続化済みの Batch Job 実行参照を照会したが対象が存在しない
- **THEN** バックエンドは実行消失として識別可能なエラーを返し、run を成功扱いにしない

#### Scenario: Batch APIが一時的に利用できない
- **WHEN** 状態照会が一時的なネットワーク、quota、またはサービス利用不能エラーになる
- **THEN** バックエンドは再試行可能なエラーを返し、制御側は run の終端状態を変更しない

### Requirement: Batch Jobを冪等に開始・キャンセルする
Google Cloud Batch バックエンドは同じ run ID への開始要求が再送されても重複 Job によるエージェント処理を発生させてはならず（MUST NOT）、active な Batch Job のキャンセルと終端済み Job への再キャンセルを共通契約に従って処理しなければならない（SHALL）。

#### Scenario: 同じrunの開始を再送する
- **WHEN** 既に Batch Job 実行参照を持つ run ID への開始要求が再送される
- **THEN** システムは既存の実行参照を返し、新しい Batch Job でエージェント処理を開始しない

#### Scenario: 実行中のBatch Jobをキャンセルする
- **WHEN** active な Batch Job のキャンセルを要求する
- **THEN** バックエンドは対象 Job の削除による停止を要求し、停止確認後に共通状態 `cancelled` を返す

#### Scenario: 終端済みBatch Jobを再キャンセルする
- **WHEN** 成功、失敗、またはキャンセル済みの Batch Job へキャンセルを再送する
- **THEN** バックエンドは元の終端結果を変更せず、冪等に現在状態を返す

### Requirement: GKE Jobでrunを実行する
GKE バックエンドは 1 run を `claude-agent` namespace 内の一意な Kubernetes Job として開始し、Job コンテナへ run ID と必要最小限の非秘密設定だけを渡さなければならない（SHALL）。入力メッセージ、サービスアカウント鍵、アクセストークンを引数、環境変数、または Kubernetes Secret として渡してはならず（MUST NOT）、コンテナは既存の Firestore と GCS から run と workspace を取得しなければならない（SHALL）。

#### Scenario: GKE Jobを開始する
- **WHEN** 有効な run の開始を GKE バックエンドへ要求する
- **THEN** バックエンドは run ID から再現可能で衝突しない名前の Job を作成し、namespace と Job 名を含む実行参照を返す

#### Scenario: GKE Jobコンテナへ入力を渡す
- **WHEN** 作成された Job の Pod が起動する
- **THEN** コンテナは渡された run ID を使って Firestore から入力を、GCS から workspace を取得し、他の実行基盤と同じ JobRunner を実行する

### Requirement: GKE Jobの状態を共通状態へ正規化する
GKE バックエンドは Job と Pod の未スケジュール、実行中、成功、失敗、削除状態を共通契約の `pending`、`running`、`succeeded`、`failed`、`cancelled` のいずれかへ正規化しなければならない（SHALL）。状態取得の一時的な失敗、権限不足、Job 消失を区別しなければならない（SHALL）。

#### Scenario: GKE Jobの状態を照会する
- **WHEN** 制御側が保存済みの GKE Job 実行参照を照会する
- **THEN** バックエンドは Kubernetes 固有状態を共通状態へ変換し、Kubernetes クライアントのオブジェクトを公開しない

#### Scenario: GKE Jobが見つからない
- **WHEN** 永続化済みの GKE Job 実行参照を照会したが対象が存在しない
- **THEN** バックエンドは実行消失として識別可能なエラーを返し、run を成功扱いにしない

#### Scenario: Kubernetes APIが一時的に利用できない
- **WHEN** 状態照会が一時的なネットワーク、API server の過負荷、またはサービス利用不能エラーになる
- **THEN** バックエンドは再試行可能なエラーを返し、制御側は run の終端状態を変更しない

#### Scenario: Kubernetes APIへの権限が不足する
- **WHEN** Job の作成、照会、または削除が認可エラーになる
- **THEN** バックエンドは権限不足として識別可能なエラーを返し、別種の一時エラーとして扱わない

### Requirement: GKE Jobを冪等に開始・キャンセルする
GKE バックエンドは同じ run ID への開始要求が再送されても重複 Job によるエージェント処理を発生させてはならず（MUST NOT）、active な Job を foreground deletion で停止し、終端済み Job への再キャンセルを共通契約に従って処理しなければならない（SHALL）。削除によりキャンセル済み Job が消失しても、明示的なキャンセル結果と予期しない実行消失を区別できなければならない（SHALL）。

#### Scenario: 同じrunの開始を再送する
- **WHEN** 既に GKE Job 実行参照を持つ run ID への開始要求が再送される
- **THEN** システムは既存の実行参照を返すか同名 Job を同じ実行として扱い、新しいエージェント処理を開始しない

#### Scenario: 実行中のGKE Jobをキャンセルする
- **WHEN** active な GKE Job のキャンセルを要求する
- **THEN** バックエンドは配下の Pod を含む Job の削除を要求し、停止確認後に共通状態 `cancelled` を返す

#### Scenario: 終端済みGKE Jobを再キャンセルする
- **WHEN** 成功、失敗、またはキャンセル済みの GKE Job へキャンセルを再送する
- **THEN** バックエンドは元の終端結果を変更せず、冪等に現在状態を返す

### Requirement: GKE Jobへtolerationsを設定する
GKE バックエンドは、リリース設定で指定された 0 個以上の toleration を、各 GKE Job の Pod テンプレートへ適用しなければならない（SHALL）。各 toleration は `key`、`operator`、`value`、`effect` を保持し、`operator` は `Equal` または `Exists`、`effect` は `NoSchedule`、`PreferNoSchedule`、`NoExecute` のいずれかでなければならない（MUST）。`operator: Exists` の `value` は空でなければならず（MUST）、不正な設定は Kubernetes API を呼び出す前に拒否しなければならない（SHALL）。

#### Scenario: 複数のtolerationをPodテンプレートへ反映する
- **WHEN** GKE リリース設定に有効な toleration が複数指定され、run の開始が要求される
- **THEN** 作成される Job の `spec.template.spec.tolerations` は、設定された順序と各要素の `key`、`operator`、`value`、`effect` を保持する

#### Scenario: tolerationsを指定せず既存動作を維持する
- **WHEN** GKE リリース設定に tolerations が指定されていない状態で run の開始が要求される
- **THEN** バックエンドは tolerations によるスケジューリング制約を追加せず、従来と同じ Job を作成する

#### Scenario: Exists operatorを反映したPodを確認する
- **WHEN** 空の `value` と `operator: Exists` を持つ toleration で Job がクラスタに受理され Pod が作成される
- **THEN** Kubernetes API から取得した Pod の YAML に、指定した `key`、`operator`、空の `value`、`effect` が反映されている

#### Scenario: 不正なtolerationを拒否する
- **WHEN** 未対応の operator または effect、もしくは空でない `value` を持つ `operator: Exists` が設定される
- **THEN** システムはリリース設定を構成エラーとして拒否し、GKE Job を作成しない
