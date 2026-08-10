## ADDED Requirements

### Requirement: GKE Jobでrunを実行する
GKE バックエンドは 1 run を `claude-agent` namespace 内の一意な Kubernetes Job として開始し、Job コンテナへ run ID と必要最小限の非秘密設定だけを渡さなければならない（SHALL）。入力メッセージ、サービスアカウント鍵、アクセストークンを引数、環境変数、または Kubernetes Secret として渡してはならず（MUST NOT）、コンテナは既存の Firestore と GCS から run と workspace を取得しなければならない（SHALL）。

#### Scenario: GKE Jobを開始する
- **WHEN** 有効な run の開始を GKE バックエンドへ要求する
- **THEN** バックエンドは run ID から再現可能で衝突しない名前の Job を作成し、namespace と Job 名を含む実行参照を返す

#### Scenario: GKE Jobコンテナへ入力を渡す
- **WHEN** 作成された Job の Pod が起動する
- **THEN** コンテナは渡された run ID を使って Firestore から入力を、GCS から workspace を取得し、他の実行基盤と同じ JobRunner を実行する

### Requirement: GKE Jobの状態を共通状態へ正規化する
GKE バックエンドは Job と Pod の未スケジュール、実行中、成功、失敗、削除状態を共通契約の `pending`、`running`、`succeeded`、`failed`、`cancelled` のいずれかへ正規化しなければならない（SHALL）。状態取得の一時的失敗、権限不足、Job 消失を区別しなければならない（SHALL）。

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
