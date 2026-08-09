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
ドキュメントはローカルテスト、コンテナ build、Terraform 適用、Job 配備、CLI によるストリーミング確認、Streamlit 接続、Slack App の設定と Bot 起動、セッション再訪、リアルタイム応答、キャンセル、障害確認の手順を説明しなければならない（SHALL）。移行後のサンプルパスと必要な設定値を示し、秘密値をソースコードへ保存するよう案内してはならない（MUST NOT）。

#### Scenario: 初回利用者が手順を実行する
- **WHEN** 利用者が前提条件を満たして説明どおりに操作する
- **THEN** ソースコードを変更せずサンプルの開始、応答表示、再訪、キャンセルを確認できる

#### Scenario: 初回利用者がCLIとStreamlitを確認する
- **WHEN** 利用者が前提条件を満たして説明どおりに CLI と Streamlit を起動する
- **THEN** ソースコードを変更せずサンプルの開始、ストリーミング応答、再訪、キャンセルを確認できる

#### Scenario: 初回利用者がSlack Botを起動する
- **WHEN** 利用者が説明どおりに Slack App の権限とイベント購読を設定し、認証情報を環境変数で与えて Bot を起動する
- **THEN** ソースコードまたは設定ファイルへ秘密値を書き込まず、Slack スレッドで応答と会話継続を確認できる

### Requirement: サンプルのFirestore databaseを一貫して選択する
Streamlit サンプルとCloud Run Jobは同じ検証済みリリース設定からFirestore database名を取得し、その名前付き database へ接続しなければならない（SHALL）。サンプルは設定不在時または `(default)` 指定時に `(default)` databaseへ暗黙に接続してはならない（MUST NOT）。

#### Scenario: サンプル設定で接続する
- **WHEN** 利用者が `firestore_database: claude-agent-chat` を含むサンプルのリリース設定でUIを起動し、runを開始する
- **THEN** UIと起動されたJobはともに `claude-agent-chat` のsession、run、eventを読み書きする

#### Scenario: database設定が欠ける
- **WHEN** 利用者がFirestore database名を欠く、空の、または `(default)` のリリース設定でUIまたはデプロイを開始する
- **THEN** 処理は接続またはクラウド変更の前に設定エラーとして失敗する

### Requirement: サンプルを用途別のパッケージへ分離する
サンプルはジョブ用エージェント、Streamlit フロントエンド、Slack Bot フロントエンドを `example/agent`、`example/streamlit_frontend`、`example/slackbot_frontend` の独立したパッケージとして提供しなければならない（SHALL）。各フロントエンドは同じ共通チャットクライアントを利用し、ジョブ開始やイベント購読の振る舞いを個別実装してはならない（MUST NOT）。

#### Scenario: 各サンプルを個別に起動する
- **WHEN** 利用者がドキュメントに記載された各サンプルの起動コマンドを実行する
- **THEN** エージェント、Streamlit、Slack Bot は用途別のパッケージから起動し、フロントエンドは共通チャットクライアントを通じて run を操作する

### Requirement: Slackスレッドでエージェントと会話する
Slack Bot サンプルは、対象となる Slack メッセージを受信すると送信者を識別して会話 run を開始し、エージェント応答を元メッセージと同じ Slack スレッドへ段階的に反映しなければならない（SHALL）。Bot 自身が投稿したイベントを新しい入力として処理してはならない（MUST NOT）。

#### Scenario: Botへ新しいメッセージを送る
- **WHEN** 利用者が設定済みの Slack チャンネルで Bot に新しいメッセージを送る
- **THEN** Bot は利用者を識別して新しい会話 run を開始し、受領表示と応答を元メッセージのスレッドへ投稿する

#### Scenario: 応答イベントを受信する
- **WHEN** 実行中の run から複数の応答イベントが到着する
- **THEN** Bot は Slack API の制限を守りながら同じスレッドの応答を増分更新し、終端状態を最終表示へ反映する

#### Scenario: Bot自身の投稿イベントを受信する
- **WHEN** Slack が Bot 自身の投稿または更新に対応するイベントを配信する
- **THEN** Bot はそのイベントを無視し、新しい run を開始しない

#### Scenario: run開始に失敗する
- **WHEN** Slack メッセージに対する run の開始が失敗する
- **THEN** Bot は秘密情報や内部例外を含まないエラー案内を同じスレッドへ投稿する

### Requirement: Slackスレッドと会話セッションを継続可能に対応付ける
Slack Bot サンプルは Slack workspace、channel、および thread の組を会話セッションへ対応付け、同じスレッドの後続メッセージを既存セッションの新しい run として開始しなければならない（SHALL）。この対応はプロセス再起動後も復元でき、Slack 利用者ごとのアプリケーションユーザー ID を一貫して使用しなければならない（SHALL）。

#### Scenario: 同じスレッドへ返信する
- **WHEN** 利用者が過去に run を開始した Slack スレッドへ後続メッセージを投稿する
- **THEN** Bot は対応済みのセッション ID を使用して新しい run を開始し、過去の会話文脈を継続する

#### Scenario: Botを再起動してから返信する
- **WHEN** Bot プロセスの再起動後に既存 Slack スレッドへメッセージが投稿される
- **THEN** Bot は永続化済みの対応からセッション ID を復元し、同じ会話を継続する

#### Scenario: 異なる利用者がメッセージを送る
- **WHEN** 異なる Slack 利用者が Bot を利用する
- **THEN** Bot は workspace と Slack user ID に基づく別々のアプリケーションユーザー ID を使用し、他者のセッションへアクセスさせない
