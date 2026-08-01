## Purpose

利用者が最小構成の Claude Agent SDK エージェントをコンテナとして起動し、StreamlitからAgent Platform経由で長時間runを開始・再訪・キャンセルできる実行可能なサンプルを提供する。

## ADDED Requirements

### Requirement: サンプルエージェントを提供する
サンプルは Claude Agent SDK エージェントを定義し、ホスティングアダプターへ登録して API サーバーとして起動できなければならない（SHALL）。サンプル固有の業務ロジックは最小限でなければならない（SHALL）。

#### Scenario: サンプル API の起動
- **WHEN** 利用者が記載された環境変数を設定してサンプルを起動する
- **THEN** Agent Platform互換エンドポイントが利用可能になり、通常・ストリーミング・非同期実行を確認できる

### Requirement: デプロイ可能な Docker サンプルを提供する
サンプル Dockerfile は必要な Python 依存関係、アダプター、サンプルエージェントを含み、Agent Platform が要求するポートと起動コマンドで非 root ユーザーとして実行できなければならない（SHALL）。

#### Scenario: コンテナをビルドして起動する
- **WHEN** 利用者がサンプルイメージをビルドして必要な環境設定を与える
- **THEN** コンテナは非 root ユーザーで API サーバーを起動し、ヘルスチェックと推論リクエストに応答する

### Requirement: Streamlitで利用者名を指定する
StreamlitサンプルはGoogleアカウント名として扱うユーザー IDを手入力でき、すべてのSessionおよびrun操作に同じ値を渡さなければならない（SHALL）。認証済みユーザー情報の取得境界を分離し、将来Identity-Aware Proxyが提供するGoogleアカウントへ手入力値を置き換えられるようにしなければならない（SHALL）。

#### Scenario: アカウント名を入力する
- **WHEN** 利用者がStreamlitへアカウント名を入力してSessionを開始する
- **THEN** フロントエンドは当該値をユーザー IDとしてSession作成、一覧、run開始へ渡す

### Requirement: Streamlitから非同期runを管理する
Streamlitサンプルは長時間runを非同期で開始し、run IDとoperation名を保持して、状態、主要イベント、最終結果を表示し、実行中runをキャンセルできなければならない（SHALL）。

#### Scenario: 長時間runを開始する
- **WHEN** 利用者が有効なメッセージで分析開始を選択する
- **THEN** フロントエンドは非同期runを開始し、run ID、状態、永続化された主要イベントを表示する

#### Scenario: active runがある
- **WHEN** 同じSessionにactive runが存在する状態で新しいrunを開始しようとする
- **THEN** フロントエンドは新規開始を無効化し、既存runの状態と明示的なキャンセル操作を表示する

#### Scenario: runをキャンセルする
- **WHEN** 利用者がactive runのキャンセルを選択する
- **THEN** フロントエンドはキャンセル要求済みと停止完了を区別して表示し、停止完了まで新しいrunを開始させない

### Requirement: Sessionとrunを再訪する
Streamlitサンプルはユーザー IDとセッション IDを保持し、利用可能なSession一覧APIがある場合は過去Sessionの選択肢を表示しなければならない（SHALL）。画面を開き直した場合はSession EventsとOperation状態から最新runを復元しなければならない（SHALL）。

#### Scenario: ブラウザを閉じて再度開く
- **WHEN** 非同期runの実行中にブラウザを閉じ、同じユーザー IDとSessionで再度画面を開く
- **THEN** フロントエンドは保存済みrun IDとoperation名を取得し、過去イベント、現在状態、新しいイベントを表示する

#### Scenario: Session一覧APIを利用できる
- **WHEN** Agent Platform Sessions SDKが要求ユーザーのSession一覧を提供する
- **THEN** フロントエンドは復元可能なSessionを選択肢として表示する

#### Scenario: 新しい会話を開始する
- **WHEN** 利用者が新規Session操作を選択し、現在のSessionにactive runが存在しない
- **THEN** フロントエンドは保持中のSession IDと会話履歴を消去し、次の応答で作成・返却されたSession IDを保持する

### Requirement: サンプルの利用手順を説明する
ドキュメントはローカル起動、コンテナビルド、Terraform適用、Agent Platformへのデプロイ、Streamlitからの接続、非同期runの開始・再訪・キャンセル、Session StoreとClaude transcriptの比較に必要な設定とコマンドを説明しなければならない（SHALL）。

#### Scenario: 初回利用者が手順を実行する
- **WHEN** 利用者が前提条件を満たしてドキュメントの手順を順に実行する
- **THEN** ソースコードの変更を必須とせず、サンプルエージェントの非同期実行、再訪、キャンセル、状態比較を確認できる
