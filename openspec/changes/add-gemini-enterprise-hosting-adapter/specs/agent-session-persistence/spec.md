## Purpose

Agent Platform Sessions を会話、主要イベント、run状態のミラーとして利用し、GCSに保存したClaude transcriptを正本としてステートレスな実行インスタンス間で会話を継続・検証可能にする。

## ADDED Requirements

### Requirement: ユーザーとセッションを分離する
システムは、呼び出し元から渡されたユーザー ID と Agent Platform Session のリソース名を組として検証し、異なる組の会話データを相互に参照できないようにしなければならない（MUST）。ユーザーの認証とGoogleアカウントの特定はフロントエンドまたは前段のIdentity-Aware Proxyの責務としなければならない（SHALL）。

#### Scenario: 同じセッションを再利用する
- **WHEN** 同じユーザー ID と返却済みセッション ID の組で後続リクエストを受信する
- **THEN** システムは同じ Agent Platform Session を取得する

#### Scenario: 他ユーザーが同じセッション ID を指定する
- **WHEN** 異なるユーザーが既存の Agent Platform Session リソース名を指定する
- **THEN** システムは所有者不一致として拒否し、既存ユーザーのイベントを返さない

### Requirement: セッションを作成または復元する
システムは有効なセッション ID が指定されれば対応する Agent Platform Session を復元し、セッション ID が省略されれば要求ユーザーに属する新規 Session を作成してそのリソース名を返さなければならない（SHALL）。存在しない ID を暗黙に新規作成してはならない（MUST NOT）。

#### Scenario: 初回リクエスト
- **WHEN** ユーザー ID が指定され、セッション ID が省略されている
- **THEN** システムは新しい Agent Platform Session を作成し、そのリソース名と空の履歴を返す

#### Scenario: 継続リクエスト
- **WHEN** 復元可能期間内のSessionと、Sessionが参照するClaude transcriptが存在する
- **THEN** システムはSessionの主要イベントとGCSのClaude transcriptを復元し、Claude Agent SDKが会話を再開できる状態を返す

#### Scenario: 存在しないセッション ID
- **WHEN** セッション ID が指定されているが対応する Session が存在しない
- **THEN** システムは not-found エラーを返し、新しい Session を作成しない

#### Scenario: 復元可能期間を過ぎている
- **WHEN** 最終更新から設定済み復元可能期間を過ぎたSessionの復元を要求する
- **THEN** システムはsession-expiredエラーを返し、既定では1日を復元可能期間として使用する

### Requirement: 利用可能なSession一覧を取得する
システムは、利用するAgent Platform Sessions SDKがユーザー別Session一覧を提供する場合、その機能を介して要求ユーザーが復元可能なSessionを列挙できなければならない（SHALL）。SDKが一覧機能を提供しない場合は、一覧を推測せずunsupportedエラーを返さなければならない（MUST）。

#### Scenario: Session一覧APIを利用できる
- **WHEN** SDKがSession一覧を提供し、ユーザーが過去のSession一覧を要求する
- **THEN** システムは要求ユーザーに属するSessionのみを最終更新順で返し、復元可能期間を過ぎたSessionを区別する

#### Scenario: Session一覧APIを利用できない
- **WHEN** SDKがSession一覧機能を提供しない状態で一覧を要求する
- **THEN** システムは既存Sessionを変更せず、一覧機能がunsupportedであることを返す

### Requirement: 会話、主要イベント、run状態をミラーする
システムはユーザー入力、主要な応答・ツール・進捗イベント、ClaudeセッションID、run状態を、run IDとrun内で単調増加するsequenceを付けたAgent Platform Session Eventとして追記しなければならない（SHALL）。アダプター独自のイベント件数およびpayloadサイズ上限を設けてはならない（MUST NOT）。

#### Scenario: runのイベントを保存する
- **WHEN** エージェントから永続化対象の主要イベントまたはrun状態変更を受信する
- **THEN** システムはsession ID、run ID、sequence、イベント種別、発生時刻を保持してSessionへ追記する

#### Scenario: runのイベントを再取得する
- **WHEN** 切断後のクライアントがrun IDと最後に取得したsequenceを指定する
- **THEN** システムは当該sequenceより後の保存済みイベントを順序どおり返す

### Requirement: runの識別子と状態を永続化する
システムはsession IDごとにrun ID、operation名、ClaudeセッションID、workspace ID、開始・更新・完了時刻、状態、最終エラーを関連付けなければならない（SHALL）。状態は少なくともrunning、cancel_requested、cancelled、completed、failed、timed_outを区別しなければならない（MUST）。

#### Scenario: active runを復元する
- **WHEN** 別のフロントエンドまたは実行インスタンスがSessionを再度開く
- **THEN** システムはSession Eventsから最新runとoperation名を取得し、Operation APIの状態と照合できる

### Requirement: Claude transcriptを正本として保存・復元する
システムはClaude Agent SDKのtranscriptをGCSに保存し、別インスタンスでの再開時は保存済みtranscriptをClaude Agent SDKへ復元しなければならない（SHALL）。Agent Platform Sessionのミラーだけを再開の唯一の根拠としてはならない（MUST NOT）。

#### Scenario: 別インスタンスで再開する
- **WHEN** 別の実行インスタンスが同じSessionの後続runを開始する
- **THEN** システムはcommitted runが参照するGCS transcriptを復元し、そのClaudeセッションIDで会話を再開する

#### Scenario: transcriptのSDKバージョンが非互換である
- **WHEN** 保存済みtranscriptを現在のClaude Agent SDKで読み込めない
- **THEN** システムは互換変換を試みず、明示的なsession-incompatibleエラーを返す

### Requirement: SessionミラーとClaude transcriptを比較できる
システムはAgent Platform Sessionにミラーされた会話イベントとGCSのClaude transcriptを変更せずに比較し、対応件数、欠落、追加、順序差、内容差を含む検証結果を生成できなければならない（SHALL）。

#### Scenario: 同一runを比較する
- **WHEN** 利用者がcommitted runのSessionミラーとClaude transcriptの比較を要求する
- **THEN** システムは両方を読み取り、対応したイベントと乖離したイベントを識別できる検証結果を返す

### Requirement: Session Store 障害を明示する
システムは Session の取得、作成、一覧、イベント追記に失敗した場合に処理を成功扱いせず、再試行可能性を区別できる永続化エラーを呼び出し元へ返さなければならない（SHALL）。

#### Scenario: イベント追記に失敗する
- **WHEN** Agent Platform Sessions API がイベント追記を拒否する、または一時的に利用不能になる
- **THEN** システムはrunを永続化済みとして報告せず、秘密情報を除いたエラーを返す
