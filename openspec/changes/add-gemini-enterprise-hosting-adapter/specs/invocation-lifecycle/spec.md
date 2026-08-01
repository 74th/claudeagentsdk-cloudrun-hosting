## Purpose

Session、run、Long-running Operation、Claude session、workspaceを一貫して関連付け、長時間のClaude Agent SDK実行を接続状態に依存せず安全に開始・継続・停止・永続化する。

## ADDED Requirements

### Requirement: 実行単位の識別子を分離する
システムは会話単位のsession ID、1回の実行単位のrun ID、非同期実行追跡用operation名、Claude Agent SDKのClaudeセッションID、作業領域のworkspace IDを別々に管理し、run IDを介して関連付けなければならない（SHALL）。

#### Scenario: 新しいrunを開始する
- **WHEN** 既存Sessionで後続の非同期実行を開始する
- **THEN** システムはsession IDを再利用し、新しいrun IDを発行してoperation名、ClaudeセッションID、workspace IDとの対応を保存する

### Requirement: 1Sessionのactive runを1件に制限する
システムは1つのsession IDにrunningまたはcancel_requestedのrunを同時に複数存在させてはならない（MUST NOT）。active runが存在する場合、新しいrunを自動的に開始または既存runを自動的にキャンセルしてはならない（MUST NOT）。

#### Scenario: active runが存在する
- **WHEN** active runが存在するSessionへ新しいrunの開始要求を受信する
- **THEN** システムは既存run ID、operation名、状態を返して開始を拒否し、明示的なキャンセルが必要であることを通知する

#### Scenario: 既存runの停止後に開始する
- **WHEN** 既存runへのキャンセル要求後、Operation APIで停止完了を確認してから新しいrunを要求する
- **THEN** システムは新しいrun IDを発行して実行を開始する

### Requirement: 永続状態を復元してからエージェントを実行する
システムは各runで、ユーザーとSessionの解決、committed runのClaude transcript、Sessionミラー、workspace snapshotの復元または初期化を完了した後にのみエージェントを実行しなければならない（SHALL）。

#### Scenario: 継続Sessionを実行する
- **WHEN** Claude transcript、Sessionミラー、workspace snapshotが保存されているSessionで後続runを開始する
- **THEN** システムはcommitted runが参照する状態を復元し、ClaudeセッションIDと作業ディレクトリをエージェントへ渡す

#### Scenario: 復元処理が失敗する
- **WHEN** Session、Claude transcript、またはworkspace snapshotの復元が失敗する
- **THEN** システムはエージェントを実行せず、失敗した段階を識別できるエラーを返す

### Requirement: 長時間runを接続から分離する
システムは非同期runをLong-running Operationとして実行し、呼び出し元のHTTP接続が切断されてもrunを継続しなければならない（SHALL）。進捗の再表示は切断済みストリームではなく、永続化済みSession EventsとOperation状態から行わなければならない（SHALL）。

#### Scenario: 実行中にブラウザを閉じる
- **WHEN** 非同期runがrunningの間にブラウザ接続が終了する
- **THEN** システムはrunを継続し、再接続時にrun状態と最後に取得したsequence以降のイベントを返せる

### Requirement: エージェントイベントを永続化する
システムはClaude Agent SDKが生成したイベントをrun IDと単調増加するsequenceを持つイベントへ正規化し、主要イベントをSession Storeへ追記しなければならない（SHALL）。

#### Scenario: 複数のイベントを生成する
- **WHEN** エージェントが複数の応答、ツール、進捗イベントを生成する
- **THEN** システムはrun内の順序を保持して主要イベントをSession Storeへ記録する

### Requirement: 実行時間と無活動時間を制限する
システムはrunの最大実行時間と、Claude Agent SDKから対象イベントを受信しないidle timeoutを別々に監視しなければならない（SHALL）。既定値はいずれも30分とし、APIインスタンス設定で変更可能にしなければならない（SHALL）。

#### Scenario: 最大実行時間を超える
- **WHEN** runが設定済み最大実行時間までに完了しない
- **THEN** システムは停止を要求し、runをtimed_outとして記録して成功完了を返さない

#### Scenario: idle timeoutを超える
- **WHEN** runningのrunで設定済みidle timeoutの間、対象となるSDKイベントを受信しない
- **THEN** システムは停止を要求し、idle timeoutを理由とするtimed_out状態を記録する

### Requirement: runを明示的にキャンセルする
システムはユーザーまたは管理者からのキャンセル要求をLong-running Operationへ伝達し、要求時のcancel_requestedと実際の停止後のcancelledを区別して保存しなければならない（SHALL）。

#### Scenario: キャンセル要求を受け付ける
- **WHEN** runningのrunへキャンセル要求を受信する
- **THEN** システムはrunをcancel_requestedとして記録し、Operationの停止要求を送る

#### Scenario: キャンセル完了を確認する
- **WHEN** Operation APIが対象runの停止を確認する
- **THEN** システムはrunをcancelledへ更新し、一時workspaceを削除して次のrunを開始可能にする

### Requirement: 成功状態を参照付きで確定する
システムはエージェント正常終了後、Claude transcriptを含む不変workspace snapshotをGCSへ保存し、そのオブジェクト参照、generation、ハッシュを含むcommittedイベントをSession Storeへ追記してからrunをcompletedにしなければならない（SHALL）。

#### Scenario: すべての処理が成功する
- **WHEN** エージェント実行、GCS snapshot保存、Session committedイベント追記がすべて成功する
- **THEN** システムはrunをcompletedとして通知し、次回runからcommittedイベントが参照する状態を復元できる

#### Scenario: committedイベント追記に失敗する
- **WHEN** GCS snapshot保存後にSession committedイベントの追記が失敗する
- **THEN** システムはrunを成功完了として通知せず、snapshotを未commitとして残して後続の復元対象から除外する

### Requirement: 安全な外部API操作だけを再試行する
システムはSession Store、GCS、Operation APIの冪等または冪等キー付き操作がネットワークエラー、HTTP 408、429、または5xxで失敗した場合、既定で最大3回、指数バックオフとjitterを用いて再試行しなければならない（SHALL）。エージェント実行、認証・認可エラー、入力エラー、競合、非互換エラーを自動再試行してはならない（MUST NOT）。

#### Scenario: 一時的なGCS障害が解消する
- **WHEN** 冪等なGCS読取が一時的な5xxで失敗し、3回以内の再試行で成功する
- **THEN** システムは指数バックオフとjitterを適用して処理を継続する

#### Scenario: エージェント実行が失敗する
- **WHEN** Claude Agent SDKの実行がエラー終了する
- **THEN** システムは同じrunを自動再実行せず、failed状態と安全なエラー情報を保存する

### Requirement: 終了経路で一時状態を後処理する
システムはcompleted、failed、cancelled、timed_outの各終了経路でローカル一時workspaceを即時削除しなければならない（SHALL）。プロセス終了要求時は新規runの受付を止め、進行中runを成功扱いせず、可能な範囲で状態を保存して停止しなければならない（SHALL）。

#### Scenario: プロセス終了要求を受ける
- **WHEN** 実行インスタンスが終了シグナルを受信する
- **THEN** システムは新規runを受け付けず、進行中runの状態保存と一時workspace削除を試み、未完了runをcompletedとして記録しない
