# realtime-chat-sample Specification

## Purpose

サンプルエージェントと Streamlit UI により、セッション再訪、Cloud Run Job の起動、Firestore のリアルタイム応答、キャンセルを一連の操作として確認できるようにする。

## Requirements

### Requirement: active Runの外部異常終了を定期表示する
Streamlitサンプルはactive Runを表示している間、Run IDに紐づく外部Executionの状態を既存の定期更新サイクルで再照会し、Cloud Run Jobの失敗、停止、キャンセル、実行消失をFirestoreのRun状態へ反映して画面に表示しなければならない（SHALL）。pendingまたはrunningの間は実行中表示を継続し、再照会の一時的な失敗だけで異常終了と表示してはならない（MUST NOT）。

#### Scenario: Cloud Run Jobが失敗して停止する
- **WHEN** active RunのCloud Run Executionが失敗して停止し、次の定期更新が実行される
- **THEN** UIはRunを実行中のまま表示し続けず、失敗状態と安全なエラー情報を表示して新しいRunを開始できる状態へ戻る

#### Scenario: Executionがまだ実行中である
- **WHEN** 定期更新時点でExecutionがpendingまたはrunningである
- **THEN** UIは実行中表示を継続し、Runをfailedまたはcancelledへ変更しない

#### Scenario: 状態再照会が一時的に失敗する
- **WHEN** Cloud Run APIの状態再照会が一時的なエラーになる
- **THEN** UIはRunを直ちに異常終了とせず、次の更新サイクルで再試行する

#### Scenario: 失敗表示後に画面を再訪する
- **WHEN** 利用者が失敗検知後に同じセッションを再訪する
- **THEN** UIはFirestoreに保存された失敗状態、Run ID、Cloud Run execution ID、および安全なエラー情報を復元して表示する

### Requirement: ジョブ用サンプルエージェントを提供する
サンプルは Claude Agent SDK エージェント、workspace 初期化処理、run ID を受け取るジョブエントリーポイント、非 root で動作するコンテナイメージを提供しなければならない（SHALL）。

#### Scenario: サンプルジョブを起動する
- **WHEN** 利用者が run ID を与えてサンプルコンテナを起動する
- **THEN** コンテナは永続ストアから要求を取得し、エージェントイベントと終端状態を保存して終了する

### Requirement: サンプルUIでユーザーを識別する
Streamlit サンプルはすべてのセッションおよび run 操作へ同じユーザー ID を渡し、手入力 identity provider を将来の認証済み identity provider へ交換できなければならない（SHALL）。

#### Scenario: ユーザーIDを入力する
- **WHEN** 利用者がサンプル UI へユーザー ID を入力する
- **THEN** UI はその ID をセッション作成、一覧、再訪、run 操作へ一貫して使用する

### Requirement: セッションを一覧して再訪する
Streamlit サンプルは永続化済みセッションだけを、各項目に最終更新時刻とセッション名を表示して更新日時の降順に並べ、既存セッションの選択を提供しなければならない（SHALL）。過去セッションを選択したときは、最新 run だけでなく、そのセッションに保存された全 run の表示可能な会話イベントと現在状態を復元しなければならない（SHALL）。

#### Scenario: セッション一覧を表示する
- **WHEN** 利用者が複数の永続化済みセッションを持つ UI を開く
- **THEN** UI は「最終更新時刻 + セッション名」で各項目を識別し、最終更新時刻の新しい順に表示する

#### Scenario: 過去セッションを選択する
- **WHEN** 利用者が一覧から複数 run を持つ既存セッションを選択する
- **THEN** UI は保存済みの全会話、各 run の表示可能なイベント、最新 run 状態、active run の有無を会話順に復元して表示する

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

### Requirement: 長時間runを開始する
Streamlit サンプルは入力メッセージを永続化して非同期 run を開始し、run ID とバックエンド実行参照を保持しなければならない（SHALL）。active run がある間は新規 run を開始させてはならない（MUST NOT）。

#### Scenario: メッセージを送信する
- **WHEN** 利用者が active run のないセッションで有効なメッセージを送信する
- **THEN** UI は run を登録してジョブを開始し、接続を占有せず操作可能な状態へ戻る

### Requirement: 応答をリアルタイム表示する
Streamlit サンプルは run のイベント購読を使用してエージェント応答、ツール要約、進捗、終端状態を増分表示し、再接続時は最後のカーソル以後を再取得しなければならない（SHALL）。

#### Scenario: ジョブが応答を保存する
- **WHEN** UI が購読中にジョブが新しいイベントを保存する
- **THEN** UI は新しいイベントを順序どおり表示し、重複イベントを二重表示しない

#### Scenario: ブラウザを開き直す
- **WHEN** 実行中にブラウザを閉じて同じセッションを再訪する
- **THEN** UI は永続化済みイベントと現在の run 状態を復元し、新しいイベントの購読を継続する

### Requirement: runをキャンセルする
Streamlit サンプルは active run の明示的キャンセル操作を提供し、cancel_requested と停止完了を区別して表示しなければならない（SHALL）。

#### Scenario: キャンセルを要求する
- **WHEN** 利用者が active run のキャンセルを選択する
- **THEN** UI は要求済み状態を表示し、停止完了まで新規 run を無効にする

### Requirement: 一連の利用手順を説明する
ドキュメントはローカルテスト、コンテナ build、Terraform 適用、Job 配備、Streamlit 接続、セッション再訪、リアルタイム応答、キャンセル、障害確認の手順を説明しなければならない（SHALL）。

#### Scenario: 初回利用者が手順を実行する
- **WHEN** 利用者が前提条件を満たして説明どおりに操作する
- **THEN** ソースコードを変更せずサンプルの開始、応答表示、再訪、キャンセルを確認できる

### Requirement: サンプルのFirestore databaseを一貫して選択する
Streamlit サンプルとCloud Run Jobは同じ検証済みリリース設定からFirestore database名を取得し、その名前付き database へ接続しなければならない（SHALL）。サンプルは設定不在時または `(default)` 指定時に `(default)` databaseへ暗黙に接続してはならない（MUST NOT）。

#### Scenario: サンプル設定で接続する
- **WHEN** 利用者が `firestore_database: claude-agent-chat` を含むサンプルのリリース設定でUIを起動し、runを開始する
- **THEN** UIと起動されたJobはともに `claude-agent-chat` のsession、run、eventを読み書きする

#### Scenario: database設定が欠ける
- **WHEN** 利用者がFirestore database名を欠く、空の、または `(default)` のリリース設定でUIまたはデプロイを開始する
- **THEN** 処理は接続またはクラウド変更の前に設定エラーとして失敗する
