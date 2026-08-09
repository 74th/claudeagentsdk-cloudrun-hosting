# workspace-object-store Specification

## Purpose

セッション固有の作業ファイルと Claude transcript を安全な不変 snapshot として保存・復元し、GCS に固定されないオブジェクトストレージ契約を提供する。

## Requirements

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

### Requirement: 可変の実行パスからtranscriptを再開できる
システムは再開対象の Claude transcript を、前回実行時の一時 workspace の絶対パスに依存せず利用可能にしなければならない（SHALL）。Claude Agent SDK が提供する場合は、復元済み transcript を session store 経由でも渡さなければならない（SHALL）。

#### Scenario: 実行時workspaceのパスが変わる
- **WHEN** snapshot を作成した run と再開する run の一時 workspace パスが異なる
- **THEN** システムは復元済み transcript から保存済み Claude セッション ID の会話を読み込み、後続 run がそれまでの会話履歴を参照できる

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
システムは圧縮前後の snapshot 容量上限と、未 commit および commit 済みオブジェクトに共通する保持期間を設定可能にし、上限超過 snapshot を有効版として保存してはならない（MUST NOT）。workspace、transcript、一時 object の共通保持期間は作成から既定 30 日とし、Firestore チャットデータと同じリリース設定値を使用しなければならない（SHALL）。commit 状態または object prefix によって共通保持期間より短い削除 lifecycle を適用してはならない（MUST NOT）。

#### Scenario: snapshotが容量上限を超える
- **WHEN** snapshot の圧縮前または圧縮後容量が設定上限を超える
- **THEN** システムは snapshot を commit せず容量超過エラーを返す

#### Scenario: GCS objectが既定保持期間へ到達する
- **WHEN** workspace、transcript、または一時 object が作成から 30 日へ到達する
- **THEN** オブジェクトストアは commit 状態にかかわらず当該 object を自動削除の対象にする

#### Scenario: 設定した保持期間をGCSへ適用する
- **WHEN** リリース設定で有効な共通保持期間を指定する
- **THEN** workspace、transcript、および一時 object はすべて作成から指定日数後に自動削除の対象となる

### Requirement: オブジェクトストア実装を交換できる
snapshot の保存、取得、存在確認、削除は GCS 固有型を公開契約へ漏らさず、条件付き作成と version 検証を提供する別ストア実装へ交換できなければならない（SHALL）。

#### Scenario: インメモリストアで検証する
- **WHEN** 利用者がテスト用オブジェクトストアを構成する
- **THEN** lifecycle は GCS 接続なしで保存、復元、競合、破損の振る舞いを検証できる

### Requirement: runごとにworkspace setupを適用する
システムは新規 workspace の初期化または committed snapshot の復元が完了した後、Agent 実行前に登録済み workspace setup を毎回適用しなければならない（SHALL）。workspace setup は snapshot の有無にかかわらず実行し、初回だけ実行する initializer とは別の契約でなければならない（SHALL）。

#### Scenario: 新規workspaceをセットアップする
- **WHEN** committed snapshot がない run を開始し initializer と workspace setup が登録されている
- **THEN** システムは空の workspace に initializer を適用した後、workspace setup を一度適用してから Agent を開始する

#### Scenario: 復元済みworkspaceをセットアップする
- **WHEN** committed snapshot がある後続 run を開始する
- **THEN** システムは snapshot を復元した後、workspace setup を一度適用してから Agent を resume する

#### Scenario: workspace setupが失敗する
- **WHEN** workspace setup が例外を返す
- **THEN** システムは Agent を開始せず、run を成功扱いせずに一時 workspace を後処理する
