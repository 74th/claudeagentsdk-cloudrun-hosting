## ADDED Requirements

### Requirement: Google Cloud Batchでrunを実行する
Google Cloud Batch バックエンドは 1 run を一意な 1 Batch Job として開始し、ジョブコンテナへ run ID と必要最小限の非秘密設定だけを渡さなければならない（SHALL）。入力メッセージや認証情報をコマンド引数または環境変数へ埋め込んではならず（MUST NOT）、コンテナは既存の Firestore と GCS から run と workspace を取得しなければならない（SHALL）。

#### Scenario: Batch Jobを開始する
- **WHEN** 有効な run の開始を Google Cloud Batch バックエンドへ要求する
- **THEN** バックエンドは run ID から再現可能かつ衝突しない Job ID を持つ Batch Job を作成し、そのリソース名を実行参照として返す

#### Scenario: Batchコンテナへ入力を渡す
- **WHEN** 作成された Batch Job のコンテナが起動する
- **THEN** コンテナは渡された run ID を使って Firestore から入力を、GCS から workspace を取得し、Cloud Run 実行時と同じ JobRunner を実行する

### Requirement: Batch Jobの状態を共通状態へ正規化する
Google Cloud Batch バックエンドは Batch Job の queued、scheduled、running、成功、失敗、削除／キャンセル状態を、共通契約の `pending`、`running`、`succeeded`、`failed`、`cancelled` のいずれかへ正規化しなければならない（SHALL）。状態取得の一時的失敗と Job 消失は区別しなければならない（SHALL）。

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
