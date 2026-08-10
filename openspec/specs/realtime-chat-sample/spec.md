# realtime-chat-sample Specification

## Purpose

サンプルエージェントと Streamlit UI により、セッション再訪、Cloud Run Job の起動、Firestore のリアルタイム応答、キャンセルを一連の操作として確認できるようにする。

## Requirements

### Requirement: Streamlitの実行中表示を部分更新する
Streamlit サンプルは active Run の定期状態確認中、会話イベント、実行状態、質問、タスク進捗、および Run 操作を含む動的領域だけを更新し、セッション一覧、設定、およびその他の動的領域外の表示を周期的に再描画してはならない（MUST NOT）。Run が終端状態へ到達したときは画面全体を一度更新し、セッション一覧を含む表示を永続化済みの最新状態と同期しなければならない（SHALL）。

#### Scenario: Agentが処理中である
- **WHEN** active Run が pending または running のまま次の定期更新時刻に達する
- **THEN** UI は実行中に変化する動的領域だけを最新状態へ更新し、サイドバーのセッション一覧や設定領域を再描画しない

#### Scenario: プロンプトを送信した直後である
- **WHEN** 利用者が新しいプロンプトを送信し、Run の開始処理または最初のイベント保存が完了していない
- **THEN** UI は送信済みのユーザープロンプトを固定領域に直ちに表示し、agent の応答・ツール・進捗以降だけをfragmentの動的領域で更新する

#### Scenario: 初めて画面を開く
- **WHEN** セッション選択とdraft状態がSession Stateにまだ存在しない
- **THEN** UI は利用者が `New session` を押した場合と同じ新規draft画面を表示する

#### Scenario: 新しいイベントが保存される
- **WHEN** active Run の処理中に新しい応答、ツール、進捗、またはタスクイベントが保存される
- **THEN** UI は次の部分更新でイベントを順序どおり動的領域へ反映し、画面全体を再描画しない

#### Scenario: 新しい変更がない
- **WHEN** 定期更新で確認した Session、Run、および最新イベントの revision が前回表示時と同じである
- **THEN** UI は履歴全体と interaction を再取得せず、前回の動的表示を維持する

#### Scenario: Runが終了する
- **WHEN** 部分更新が finish イベントまたは Run の終端状態を検出する
- **THEN** UI は画面全体を一度更新し、会話の最終表示とサイドバーのセッション名、更新日時、および選択状態を最新状態へ同期する

#### Scenario: 質問への回答を待っている
- **WHEN** active Run に未回答の質問があり、利用者が回答ウィジェットを操作できる状態になる
- **THEN** UI は自動部分更新を停止し、利用者の選択または入力を定期更新によって失わせない

#### Scenario: 状態確認が一時的に失敗する
- **WHEN** active Run の定期状態確認が一時的なエラーになる
- **THEN** UI は動的領域で実行中表示を維持して次の部分更新で再試行し、画面全体を更新しない

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
サンプルは Claude Agent SDK エージェントの system prompt、model、tools、run ごとの workspace setup、構成済み Store、および run ID を受け取るジョブエントリーポイントを提供しなければならない（SHALL）。サンプルの Agent runtime は Store の永続状態遷移、workspace snapshot lifecycle、Claude transcript の再配置、または終端 commit を直接実装してはならず（MUST NOT）、これらをフレームワークの共通実行 API に委譲しなければならない（SHALL）。コンテナイメージは非 root で動作しなければならない（SHALL）。

#### Scenario: サンプルジョブを起動する
- **WHEN** 利用者が run ID を与えてサンプルコンテナを起動する
- **THEN** コンテナはサンプルで宣言した Agent 設定と workspace setup を用い、共通実行 API を通じて永続要求の取得、エージェントイベント、および終端状態を保存して終了する

#### Scenario: サンプルのAgent設定を変更する
- **WHEN** 利用者がサンプルの system prompt、model、または tools を変更する
- **THEN** 利用者は Store の読み書き、snapshot、resume、または timeout の lifecycle 実装を変更せず Agent の振る舞いを変更できる

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

### Requirement: Streamlitで選択式質問へ回答する
Streamlit サンプルは active run の pending 質問を会話内に表示し、各質問の見出し、質問文、説明付き選択肢、単一または複数選択、および「その他」の自由入力を提供しなければならない（SHALL）。回答送信後は受理済み状態を表示し、回答確定まで同じ質問を重複送信させてはならない（MUST NOT）。

#### Scenario: 単一選択質問に回答する
- **WHEN** 利用者が pending 質問の選択肢を 1 個選び回答を送信する
- **THEN** UI は同じ session と active run の質問要求へ回答し、受理後にエージェントの継続イベントを表示する

#### Scenario: 複数選択質問に回答する
- **WHEN** 利用者が複数選択可能な質問で複数の選択肢を選ぶ
- **THEN** UI は選択したラベルを重複なく回答として送信する

#### Scenario: その他を自由入力する
- **WHEN** 利用者が「その他」を選択して空でないテキストを入力する
- **THEN** UI は「その他」という表示ラベルではなく入力テキストそのものを回答として送信する

#### Scenario: 入力が無効である
- **WHEN** 利用者が選択せず、または「その他」を選んで空のまま送信する
- **THEN** UI は検証エラーを表示し、回答を保存しない

### Requirement: Slackスレッドで番号またはテキストにより回答する
Slack Bot サンプルは pending 質問を同じスレッドへ番号付き選択肢として投稿し、単一選択では `1`、複数選択では `1,3` のような番号列、または空でない任意テキストを回答として受け付けなければならない（SHALL）。pending 質問がある間の同じ利用者からの返信は回答として扱い、新しい run のプロンプトとして扱ってはならない（MUST NOT）。

#### Scenario: 数字1だけで回答する
- **WHEN** pending 単一選択質問の最初の選択肢に対して利用者が同じスレッドへ `1` と返信する
- **THEN** Bot は最初の選択肢ラベルを質問回答として送信し、新しい run を開始しない

#### Scenario: 複数番号で回答する
- **WHEN** pending 複数選択質問へ利用者が `1,3` と返信する
- **THEN** Bot は表示された 1 番目と 3 番目のラベルを選択順で回答する

#### Scenario: 任意テキストで回答する
- **WHEN** pending 質問へ利用者が選択番号ではない空でないテキストを返信する
- **THEN** Bot はそのテキスト自体を自由入力回答として送信する

#### Scenario: 番号が範囲外である
- **WHEN** 利用者が存在しない番号、空要素を含む番号列、または単一選択へ複数番号を返信する
- **THEN** Bot は有効な入力例を同じスレッドへ案内し、質問を pending のまま維持する

#### Scenario: 別の利用者が回答する
- **WHEN** pending 質問を開始した利用者とは異なる利用者が同じスレッドへ返信する
- **THEN** Bot は回答を受理せず、元の run またはセッションを変更しない

### Requirement: 両フロントエンドで最新タスクリストを表示する
Streamlit と Slack Bot は run のタスク状態を共通チャットクライアントから取得し、pending、in_progress、completed の状態、件名、および利用可能な依存関係を利用者が識別できる形で表示しなければならない（SHALL）。同じタスクの更新を履歴として無制限に重複表示せず、最新状態を反映しなければならない（SHALL）。

#### Scenario: タスクが進行する
- **WHEN** TaskCreate、in_progress への TaskUpdate、completed への TaskUpdate が順に到着する
- **THEN** 両フロントエンドは同じ Task ID を持つ 1 件の表示を pending、in_progress、completed の順に更新する

#### Scenario: 画面またはBotを再起動する
- **WHEN** 利用者が進行中 run の Streamlit 画面を再訪するか Slack Bot が再起動する
- **THEN** フロントエンドは永続イベントから最新タスクリストと pending 質問を復元して表示を継続する

#### Scenario: タスクが削除される
- **WHEN** TaskUpdate によりタスクが deleted になる
- **THEN** フロントエンドはそのタスクを現在のタスクリストから除外する

### Requirement: 対話質問とタスク進捗の利用手順を説明する
ドキュメントは Streamlit と Slack Bot で選択式質問を発生させ、定義済み選択、複数選択、自由入力、Slack の番号回答、およびタスク進捗を確認する手順を説明しなければならない（SHALL）。

#### Scenario: 利用者が対話デモを確認する
- **WHEN** 利用者が説明どおりにサンプルを起動して質問とタスク作成を促すプロンプトを送る
- **THEN** ソースコードを変更せず、両フロントエンドで質問回答後の run 再開とタスク状態更新を確認できる

### Requirement: 各フロントエンドで処理単位の推定費用と処理時間を表示する
StreamlitとSlack Botのサンプルは、runの終端イベントに推定総費用またはSDK処理時間が保存されている場合、最終結果と対応付けて取得できた項目を表示しなければならない（SHALL）。費用にはUSDと推定値であることを、処理時間には時間単位を明示しなければならず（SHALL）、欠損値を0ドルまたは0秒として表示してはならない（MUST NOT）。

#### Scenario: Streamlitで処理が完了する
- **WHEN** Streamlitが推定費用と処理時間を持つrunの最終結果を表示する
- **THEN** UIはその最終結果の近くに推定費用をUSDで、処理時間を人が読める時間単位で表示する

#### Scenario: Slack Botで処理が完了する
- **WHEN** Slack Botが推定費用と処理時間を持つrunの最終結果をスレッドへ投稿する
- **THEN** Botは同じスレッドの最終結果に両方の値とそれぞれの意味を明示する

#### Scenario: 完了済みセッションを再訪する
- **WHEN** 利用者が処理メタデータ付きの過去runをStreamlitで再訪する
- **THEN** UIは保存された各runの最終結果に対応する推定費用と処理時間を再表示する

#### Scenario: 一方の値だけ取得できる
- **WHEN** 終端イベントに推定費用または処理時間の一方だけが含まれる
- **THEN** 各フロントエンドは取得できた項目だけを表示し、欠けた項目の代替値を表示しない

#### Scenario: SDKが処理メタデータを返さない
- **WHEN** 終端イベントに推定費用も処理時間も含まれない
- **THEN** 各フロントエンドは従来どおり最終結果を表示し、処理メタデータの欠損を利用者向けエラーにしない

#### Scenario: SDKがエラー結果と処理メタデータを返す
- **WHEN** runが失敗し、エラー終端イベントに推定費用または処理時間が保存される
- **THEN** 各フロントエンドは成功した最終回答があるかのように扱わず、失敗状態と対応付けて取得できた処理メタデータを表示する
