## ADDED Requirements

### Requirement: 利用者への選択式質問を永続回答で継続する
ジョブは Claude Agent SDK が利用者への質問を要求したとき、質問要求を run の順序付きイベントとして永続化し、同じ質問要求に対する有効な回答を受信するまでエージェントセッションを維持して待機しなければならない（SHALL）。回答を受信したときは質問文をキーとする回答を SDK へ返して処理を再開しなければならず（SHALL）、回答待ち中のキャンセルまたはタイムアウト後に処理を再開してはならない（MUST NOT）。

#### Scenario: 単一選択の回答を受信する
- **WHEN** SDK が 2〜4 個の選択肢を持つ単一選択質問を要求し、利用者がそのうち 1 個へ回答する
- **THEN** ジョブは質問要求を永続化してから、質問文と選択肢ラベルの対応を SDK へ返して同じ run を再開する

#### Scenario: その他の自由入力を受信する
- **WHEN** pending 質問に対して利用者が定義済みラベルではない空でない自由入力を回答する
- **THEN** ジョブは「その他」という固定値ではなく自由入力値そのものを SDK へ返す

#### Scenario: 回答待ち中にrunがキャンセルされる
- **WHEN** 質問回答を待っている run にキャンセルまたは実行期限超過が確定する
- **THEN** ジョブは待機を解除してエージェントを停止し、後から届いた回答で run を再開しない

### Requirement: Task系ツールの操作と結果を関連付けて保存する
ジョブは `TaskCreate`、`TaskUpdate`、`TaskGet`、`TaskList` の tool use と対応する tool result を、tool use ID により関連付け可能な順序付きイベントとして逐次永続化しなければならない（SHALL）。Task ID が結果で決定または完全な一覧が結果で返される場合、その結果を完了前から購読者が利用できなければならない（SHALL）。

#### Scenario: TaskCreateでIDが決定する
- **WHEN** エージェントが `TaskCreate` を実行し、対応する tool result に新しい Task ID が含まれる
- **THEN** 保存イベントから作成入力と確定 Task ID を同じ操作として関連付けられる

#### Scenario: TaskListが完全な一覧を返す
- **WHEN** エージェントが `TaskList` を実行して対応する tool result にタスク一覧が返る
- **THEN** 購読者はその結果を run の最新タスクスナップショットとして利用できる

#### Scenario: TaskUpdateの入力キーに互換表現がある
- **WHEN** Task ID が `taskId`、`id`、または `task_id` のいずれかで tool use に現れる
- **THEN** 保存される共通情報は対象 Task ID を失わず、後続の状態復元で同じタスクへ更新を適用できる
