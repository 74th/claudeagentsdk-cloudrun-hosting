## Purpose

永続化された run 要求をジョブコンテナが安全に取得し、状態復元、エージェント実行、イベント配信、snapshot 確定、後処理までを接続状態に依存せず完結させる。

## ADDED Requirements

### Requirement: 実行単位の識別子を分離する
システムはユーザー ID、会話単位の session ID、1 回の処理単位の run ID、実行バックエンド参照、Claude Agent SDK の Claude セッション ID、workspace ID を別々に管理し、run レコードを介して関連付けなければならない（SHALL）。

#### Scenario: 継続セッションでrunを開始する
- **WHEN** 既存セッションで後続 run を開始する
- **THEN** システムは session ID と workspace ID を再利用し、新しい run ID とバックエンド実行参照を関連付ける

### Requirement: ジョブ開始時にrunの所有権を取得する
ジョブコンテナは run ID に対応する要求を検証し、原子的に実行所有権を取得した後にだけエージェントを実行しなければならない（SHALL）。別の実行が所有する run を処理してはならない（MUST NOT）。

#### Scenario: 重複ジョブが起動する
- **WHEN** 同じ run ID を持つジョブコンテナが複数起動する
- **THEN** 所有権を取得した 1 件だけがエージェントを実行し、ほかは重複として安全に終了する

### Requirement: 永続状態を復元してから実行する
システムは入力、所有者、キャンセル状態を検証し、最新 committed snapshot または新規 workspace の初期化を完了してから Claude Agent SDK を実行しなければならない（SHALL）。

#### Scenario: 継続セッションを実行する
- **WHEN** committed snapshot があるセッションの run を処理する
- **THEN** システムは workspace と Claude transcript を復元し、保存済み Claude セッション ID で処理を再開する

#### Scenario: 復元に失敗する
- **WHEN** snapshot が破損、欠落、または非互換である
- **THEN** システムはエージェントを実行せず run を復元失敗として終了する

### Requirement: エージェントイベントを逐次永続化する
システムは Claude Agent SDK の会話、ツール、進捗、エラーを正規化し、実行中に順次チャットストアへ追記しなければならない（SHALL）。最終完了までイベントをメモリだけに保持してはならない（MUST NOT）。

#### Scenario: 長時間処理が進捗を生成する
- **WHEN** エージェントが複数の応答または進捗を生成する
- **THEN** システムは処理完了前から購読者が受信できる形で各イベントを保存する

### Requirement: キャンセル要求へ協調的に応答する
ジョブは起動時およびイベント処理中に永続化済みキャンセル要求を確認し、要求を検出した場合は Claude Agent SDK へ停止を伝えて新しい成功 snapshot を commit してはならない（MUST NOT）。

#### Scenario: 実行中にキャンセルされる
- **WHEN** active run にキャンセル要求が保存される
- **THEN** ジョブは処理停止を試み、変更中 snapshot を破棄して run を cancelled として終了する

### Requirement: 正常完了をsnapshotとともに確定する
システムはエージェント正常終了後に workspace と Claude transcript の不変 snapshot を保存し、その参照と最終応答を永続化してから run を completed にしなければならない（SHALL）。

#### Scenario: snapshot保存後に状態更新が失敗する
- **WHEN** snapshot は保存できたが completed 状態の保存に失敗する
- **THEN** システムは利用者へ成功を通知せず、同じ run の再試行で既存 snapshot を検証して確定処理を再開できる

### Requirement: 実行時間と無活動時間を制限する
システムは run の最大実行時間と対象イベントを受信しない idle timeout を個別に監視し、超過した run を成功扱いしてはならない（MUST NOT）。各制限は設定可能でなければならない（SHALL）。

#### Scenario: 最大実行時間を超える
- **WHEN** run が設定済み最大実行時間までに完了しない
- **THEN** システムは停止を要求して timed_out を保存する

### Requirement: 安全な操作だけを再試行する
システムは冪等または冪等キー付きのストア・バックエンド操作だけを一時障害時に再試行し、Claude Agent SDK の実行そのものを自動再実行してはならない（MUST NOT）。

#### Scenario: イベント保存が一時的に失敗する
- **WHEN** event ID 付き追記が一時障害で失敗する
- **THEN** システムは制限付きバックオフで同じ event ID を再送する

### Requirement: すべての終了経路で後処理する
システムは completed、failed、cancelled、timed_out、重複実行の各終了経路でローカル一時 workspace を削除し、未完了 run を completed として記録してはならない（MUST NOT）。

#### Scenario: ジョブが終了シグナルを受ける
- **WHEN** 実行中のジョブコンテナが終了シグナルを受信する
- **THEN** システムは停止と状態保存を試み、一時 workspace を削除し、未確定の run を成功扱いしない

