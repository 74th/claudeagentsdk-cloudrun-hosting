## MODIFIED Requirements

### Requirement: セッションを一覧して再訪する
Streamlit サンプルは永続化済みセッションだけを、各項目に最終更新時刻とセッション名を表示して更新日時の降順に並べ、既存セッションの選択を提供しなければならない（SHALL）。過去セッションを選択したときは、最新 run だけでなく、そのセッションに保存された全 run の表示可能な会話イベントと現在状態を復元しなければならない（SHALL）。

#### Scenario: セッション一覧を表示する
- **WHEN** 利用者が複数の永続化済みセッションを持つ UI を開く
- **THEN** UI は「最終更新時刻 + セッション名」で各項目を識別し、最終更新時刻の新しい順に表示する

#### Scenario: 過去セッションを選択する
- **WHEN** 利用者が一覧から複数 run を持つ既存セッションを選択する
- **THEN** UI は保存済みの全会話、各 run の表示可能なイベント、最新 run 状態、active run の有無を会話順に復元して表示する

## ADDED Requirements

### Requirement: 空の新規セッションを遅延開始する
Streamlit サンプルは利用者が新規会話を開いた時点では入力可能な空表示だけを用意し、最初の有効なプロンプトが送信された時点でセッションと run を開始しなければならない（SHALL）。送信前の空表示をセッション一覧へ含めたり、セッション ID が決定済みであるかのように表示したりしてはならない（MUST NOT）。

#### Scenario: 新規会話を開く
- **WHEN** 利用者が New session を選択する
- **THEN** UI はセッション ID を作成せず空の会話とプロンプト入力を表示する

#### Scenario: 最初のプロンプトを送信する
- **WHEN** 利用者が空の新規会話から最初の有効なプロンプトを送信する
- **THEN** UI は名前付きセッションと最初の run を開始し、返されたセッションを選択状態にして一覧へ反映する

### Requirement: セッション名と実行識別子を区別して表示する
Streamlit サンプルは現在のセッション名を会話画面に表示し、セッション ID を Cloud Run 実行 ID の隣に表示しなければならない（SHALL）。アプリケーションの session ID、run ID、および Cloud Run 実行 ID はラベルで明確に区別しなければならない（SHALL）。

#### Scenario: Cloud Run実行が割り当てられる
- **WHEN** 現在の run に Cloud Run 実行 ID が保存されている
- **THEN** UI は現在のセッション名を表示し、Session ID と Cloud Run execution ID を隣接したラベル付きの値として表示する

#### Scenario: 実行割り当て前のセッションを表示する
- **WHEN** セッションは開始済みだが Cloud Run 実行 ID がまだ保存されていない
- **THEN** UI は Session ID と run ID を表示し、Cloud Run execution ID が未割り当てであることを別の ID と混同せず示す

