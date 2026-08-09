## ADDED Requirements

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

## MODIFIED Requirements

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
