## Purpose

ステートレスな Agent Platform 実行環境において、セッション固有の作業ファイルとClaude transcriptをGoogle Cloud Storageへ安全に退避・復元し、継続的なエージェント作業を可能にする。

## ADDED Requirements

### Requirement: ワークスペースをユーザーおよびセッション単位で分離する
システムは、ユーザー ID とセッション ID から安全なworkspace IDとストレージキーを生成し、各ワークスペースを他のユーザーおよびセッションと異なるディレクトリおよびGCS名前空間へ配置しなければならない（MUST）。外部識別子を未検証のファイルパスとして使用してはならない（MUST NOT）。このディレクトリ分離をコンテナまたはOSレベルのセキュリティ境界として保証してはならない（MUST NOT）。

#### Scenario: 異なるセッションの保存先
- **WHEN** 同じユーザーが異なる2つのセッションを実行する
- **THEN** 各セッションのワークスペースは異なるローカルディレクトリとGCSオブジェクト名前空間に保存される

#### Scenario: パストラバーサル文字を含む識別子
- **WHEN** 外部識別子に区切り文字や `..` が含まれる
- **THEN** システムは識別子を安全なキーへ変換または拒否し、割り当てられたローカルルート外へアクセスしない

### Requirement: Claude transcriptをワークスペースとともに保存する
システムはClaude Agent SDKが生成するtranscriptと再開に必要なセッションファイルを、作業ファイルと同じcommitted snapshotへ含めなければならない（SHALL）。Claudeセッションデータと利用者の作業ファイルはsnapshot内で区別可能なディレクトリへ配置しなければならない（SHALL）。

#### Scenario: Claude Agent SDKがtranscriptを更新する
- **WHEN** runの実行中にClaude Agent SDKがtranscriptまたは再開用ファイルを更新する
- **THEN** システムは正常終了時のsnapshotへ更新済みClaudeセッションデータと作業ファイルの両方を含める

### Requirement: バージョン付き不変snapshotを保存する
システムはストレージschemaバージョン、SDKバージョン、session ID、run IDを区別できるGCSパスへrunごとの不変snapshotを保存し、同じrunの既存snapshotを条件なしで上書きしてはならない（MUST NOT）。異なるschemaまたは互換性のないSDKバージョンのsnapshotを自動移行してはならない（MUST NOT）。

#### Scenario: 新しいrunのsnapshotを保存する
- **WHEN** runが正常終了して保存処理を開始する
- **THEN** システムはschemaおよびSDKバージョンを含む名前空間へrun固有のsnapshotを新規作成条件付きで保存する

#### Scenario: 非互換snapshotを復元する
- **WHEN** 現在のschemaまたはClaude Agent SDKで読み込めないsnapshotの復元を要求する
- **THEN** システムはsnapshotを変更せず、明示的な非互換エラーを返す

### Requirement: 保存済みsnapshotを安全に復元する
システムはエージェント実行前に最新committedイベントが参照するGCS snapshotを隔離された一時ディレクトリへ復元し、ファイル属性による復元先逸脱を防がなければならない（SHALL）。未commit snapshotを復元対象にしてはならない（MUST NOT）。

#### Scenario: committed snapshotがある
- **WHEN** セッションに対応するcommitted snapshotが存在する
- **THEN** システムはその内容を一時ディレクトリへ復元し、作業ディレクトリとClaudeセッションデータをエージェントへ渡す

#### Scenario: 危険なアーカイブエントリーがある
- **WHEN** 保存データに絶対パス、親ディレクトリ参照、または安全でないリンクが含まれる
- **THEN** システムは復元を中止し、ローカルルート外へファイルを書き込まない

### Requirement: 新規ワークスペースを初期化する
システムはcommitted snapshotが存在しない場合、利用者が登録した初期化フックを空の一時ディレクトリに対して一度実行しなければならない（SHALL）。

#### Scenario: 初回セッションで初期化フックがある
- **WHEN** committed snapshotがなく、初期化フックが設定されている
- **THEN** システムはエージェント実行前にフックを呼び出し、その生成物を作業ディレクトリに含める

#### Scenario: 初期化に失敗する
- **WHEN** 初期化フックがエラーを返す
- **THEN** システムはエージェントを実行せず、未初期化の内容を GCS に保存しない

### Requirement: snapshot容量を制限する
システムはClaudeセッションデータを含むsnapshotについて、圧縮前の総容量と圧縮後のオブジェクト容量をそれぞれ100 MB以下に制限しなければならない（SHALL）。アダプターは総容量制限とは別の最大ファイル数または単一ファイルサイズ制限を設けてはならない（MUST NOT）。

#### Scenario: 圧縮前容量が上限を超える
- **WHEN** snapshot対象の総容量が100 MBを超える
- **THEN** システムはGCSへ保存せず、workspace-too-largeエラーを返す

#### Scenario: 圧縮後容量が上限を超える
- **WHEN** 生成した圧縮snapshotが100 MBを超える
- **THEN** システムは当該snapshotを有効版として保存せず、workspace-too-largeエラーを返す

### Requirement: snapshotの完全性を検証する
システムはsnapshotへ内容ハッシュ、GCS generation、schemaバージョン、SDKバージョン、run IDを関連付け、復元前に保存済み内容の完全性を検証しなければならない（SHALL）。

#### Scenario: snapshotが破損している
- **WHEN** ダウンロードしたsnapshotの内容ハッシュがcommittedイベントの参照と一致しない
- **THEN** システムは展開およびエージェント実行を中止し、workspace-corruptedエラーを返す

### Requirement: 一時領域と保存データを期限削除する
システムはrunの成功または失敗にかかわらず処理終了時にローカル一時ワークスペースを即時削除しなければならない（SHALL）。committedイベントから参照されないGCS snapshotは既定で作成から3時間後に削除対象とし、すべてのGCS workspaceおよびClaude transcriptオブジェクトは既定で作成から7日後に削除対象としなければならない（SHALL）。

#### Scenario: エージェント実行が失敗する
- **WHEN** snapshot復元後にエージェントがエラー終了する
- **THEN** システムは当該runの一時ディレクトリを即時削除し、別リクエストから参照できない状態にする

#### Scenario: 未commit snapshotが残る
- **WHEN** GCS snapshot保存後にSession committedイベントの追記が失敗し、3時間が経過する
- **THEN** システムは当該snapshotがcommittedイベントから参照されないことを確認して削除対象にする

#### Scenario: 保存オブジェクトが7日を経過する
- **WHEN** workspaceまたはClaude transcriptを含むGCSオブジェクトが作成から7日を経過する
- **THEN** GCS lifecycleは当該オブジェクトを削除対象にする
