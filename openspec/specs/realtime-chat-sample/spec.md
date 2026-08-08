# realtime-chat-sample Specification

## Purpose

サンプルエージェントと Streamlit UI により、セッション再訪、Cloud Run Job の起動、Firestore のリアルタイム応答、キャンセルを一連の操作として確認できるようにする。

## Requirements

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
Streamlit サンプルはユーザーのセッション一覧を更新日時順に表示し、新規セッションの作成と既存セッションの選択を提供しなければならない（SHALL）。

#### Scenario: 過去セッションを選択する
- **WHEN** 利用者が一覧から既存セッションを選択する
- **THEN** UI は保存済み会話、最新 run 状態、active run の有無を復元して表示する

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
