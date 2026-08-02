## Purpose

セッション固有の作業ファイルと Claude transcript を安全な不変 snapshot として保存・復元し、GCS に固定されないオブジェクトストレージ契約を提供する。

## ADDED Requirements

### Requirement: ワークスペースをユーザーとセッション単位で分離する
システムはユーザー ID とセッション ID から安全な workspace ID とストレージキーを生成し、異なるユーザーまたはセッションのデータを同じ名前空間へ保存してはならない（MUST NOT）。

#### Scenario: 外部識別子に危険な文字がある
- **WHEN** 外部識別子にパス区切り文字または親ディレクトリ参照が含まれる
- **THEN** システムは安全な不透明キーへ変換または要求を拒否し、割り当てたルート外へアクセスしない

### Requirement: transcriptとworkspaceを不変snapshotへ保存する
システムは Claude transcript、再開用セッションファイル、作業ファイル、schema・SDK version、run ID、内容ハッシュを run 固有の不変 snapshot として保存し、既存 snapshot を無条件で上書きしてはならない（MUST NOT）。

#### Scenario: runが正常終了する
- **WHEN** エージェント実行後に snapshot を保存する
- **THEN** システムは transcript と workspace を区別可能な領域へ格納し、完全性検証に必要な参照を返す

### Requirement: snapshotを安全に復元する
システムは committed snapshot だけを隔離された一時ディレクトリへ復元し、絶対パス、親参照、安全でないリンク、特殊ファイルによる復元先逸脱を拒否しなければならない（SHALL）。

#### Scenario: snapshotに危険なエントリーがある
- **WHEN** 保存データに復元ルート外を指すエントリーが含まれる
- **THEN** システムは展開とエージェント実行を中止し、ルート外へファイルを書き込まない

### Requirement: snapshotの完全性と互換性を検証する
システムは復元前に内容ハッシュ、オブジェクトversion、schema version、SDK version を検証し、破損または非互換な snapshot を自動変換してはならない（MUST NOT）。

#### Scenario: 内容ハッシュが一致しない
- **WHEN** 取得した snapshot のハッシュが committed 参照と一致しない
- **THEN** システムは復元を中止して破損エラーを返す

### Requirement: 新規ワークスペースを初期化する
システムは committed snapshot が存在しない場合に限り、登録済み初期化処理を空の一時 workspace へ適用しなければならない（SHALL）。

#### Scenario: 初回セッションを実行する
- **WHEN** snapshot がなく初期化処理が構成されている
- **THEN** システムはエージェント実行前に初期化し、失敗時はエージェントを開始しない

### Requirement: snapshot容量と保持期間を制限する
システムは圧縮前後の snapshot 容量上限と、未commitおよびcommit済みオブジェクトの保持期間を設定可能にし、上限超過 snapshot を有効版として保存してはならない（MUST NOT）。

#### Scenario: snapshotが容量上限を超える
- **WHEN** snapshot の圧縮前または圧縮後容量が設定上限を超える
- **THEN** システムは snapshot を commit せず容量超過エラーを返す

### Requirement: オブジェクトストア実装を交換できる
snapshot の保存、取得、存在確認、削除は GCS 固有型を公開契約へ漏らさず、条件付き作成と version 検証を提供する別ストア実装へ交換できなければならない（SHALL）。

#### Scenario: インメモリストアで検証する
- **WHEN** 利用者がテスト用オブジェクトストアを構成する
- **THEN** lifecycle は GCS 接続なしで保存、復元、競合、破損の振る舞いを検証できる

