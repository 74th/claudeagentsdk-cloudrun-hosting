## Context

現在の Cloud Run Job は `ClaudeAgentAdapter.events()` が返す正規化イベントを `JobRunner` で逐次保存し、`ChatService` が履歴と新着を Streamlit、Slack Bot、CLI へ共通配信している。Agent SDK 呼び出しは一方向の `query()` であり、`can_use_tool` を介した利用者入力待ちを扱っていない。Task 系ツールは一般的な `tool_started` / `tool_completed` として保存されるが、両者を関連付けて最新タスクリストへ集約する層はない。

フロントエンドとジョブは別プロセスで動き、Slack Bot やブラウザは質問回答前に再起動・再接続し得る。このため、プロセスメモリ上の Future や UI セッション状態を質問・回答の正本にはできない。また、回答待ちは Job の最大実行時間を消費するため、無期限待機は行わない。

## Goals / Non-Goals

**Goals:**

- Agent SDK の 1 回の `AskUserQuestion` に含まれる 1〜4 問を永続化し、全問の回答を同じ SDK 呼び出しへ返す。
- Streamlit と Slack Bot が同じ質問・タスク状態モデルと回答 API を利用する。
- 回答の所有者検証、競合制御、冪等再送、キャンセル／期限との競合をストア境界で保証する。
- 既存の一般ツールイベントを維持したまま、Task 系イベントから最新スナップショットを決定論的に復元する。

**Non-Goals:**

- Claude Code の見た目やキー操作を完全に再現すること。
- TypeScript SDK の選択肢プレビュー、Slack Block Kit の高度なインタラクティブコンポーネント、subagent 内の質問を扱うこと。
- 回答待ちの Job を停止し、別 Job で同じ SDK コールスタックを再開すること。
- Task を独立した業務データとして編集・管理する API を提供すること。

## Decisions

### 1. 質問状態をイベント履歴とは別の run 配下レコードでも保持する

`QuestionRequest` を run 配下の永続状態として追加し、`id`、`run_id`、`ordinal`、質問文、見出し、選択肢、`multi_select`、`pending|answered`、回答、冪等性キー、作成・回答時刻、期限を持たせる。1 回の SDK 呼び出しに複数問がある場合は ordinal 順の複数レコードを作り、コールバックは全問が answered になってから質問文をキーとする `answers` を構成する。

作成と同時に `question_pending` イベントを追記し、回答確定時に `question_answered` イベントを追記する。表示履歴はイベントを使用し、回答受付可否と競合制御は `QuestionRequest` を正本にする。イベントだけから pending 状態を推測する案は、回答とキャンセルの同時更新を原子的に検証しにくいため採用しない。

質問 ID は run ID、SDK tool use の識別情報（利用可能な場合）、呼び出し内 ordinal から決定論的に作る。SDK が tool use ID をコールバックへ渡さない場合は、run 内の単調な質問呼び出し番号と canonicalized input のハッシュを使用する。これにより同じ永続化処理の再試行は同じ質問を返す一方、同文の別質問は別 ID になる。

### 2. ストアに所有者付き回答コマンドと回答待ち契約を追加する

公開制御側には `answer_question(user_id, session_id, run_id, question_id, answer, idempotency_key)` を追加する。トランザクション内で所有者、active run、pending 状態、選択数、空入力、終端状態を検証し、最初の回答だけを確定する。同一冪等性キーは同じ結果を返し、別回答は conflict とする。自由入力は定義済み label 以外の空でない文字列として保存し、UI の「その他」というラベル自体は保存しない。

ジョブ側には所有者情報を要求しない内部ポートとして、質問の冪等作成、現在状態取得、回答通知購読を追加する。Firestore watch は通知経路であり、切断時は ID 指定の再取得を行う。インメモリ実装も同じトランザクション相当の意味を Lock で再現する。

回答を通常のユーザーメッセージとして新しい run に保存する案は、待機中 SDK の `can_use_tool` へ値を返せず会話境界も壊すため採用しない。

### 3. Agent SDK 境界を双方向セッションへ変更し、質問ブローカーを注入する

`ClaudeAgentAdapter` は継続入力ストリームを維持できる `ClaudeSDKClient` を使用し、`ClaudeAgentOptions.can_use_tool` に質問ハンドラーを設定する。`AskUserQuestion` 以外の既存 permission 方針は維持する。質問ハンドラーは SDK 入力を検証して質問ブローカーへ渡し、ブローカーから全回答を受けたら `PermissionResultAllow(updated_input={questions, answers})` を返す。

質問ブローカーは質問作成・イベント発行後、回答通知を待ちながら run のキャンセル、Job の停止要求、最大実行時間を確認し、定期 heartbeat を更新する。回答待ち中は通常の agent idle timeout を一時停止するが、run 全体の max runtime は停止しない。質問に回答がないまま上限へ達した場合は run を timed_out とし、後着回答を拒否する。

SDK コールバック内で Streamlit や Slack の関数を直接呼ぶ案は、Job と UI のプロセス分離、再接続、Cloud Run 実行に適合しないため採用しない。

### 4. Task状態は保存済みtoolイベントを共通Reducerで集約する

Agent Adapter は既存の `tool_started` と `tool_completed` を保持し、tool ID、tool 名、raw input、result、error を欠落なく保存する。共通チャット層に Task reducer を追加し、Task 系の tool use と result を tool ID で相関する。

- `TaskList` の成功 result はその時点の完全スナップショットとして置換する。
- `TaskCreate` は result から確定 ID を取得して作成入力と結合する。
- `TaskUpdate` は `taskId`、`id`、`task_id` の順で対象を解決してフィールドと状態を更新する。
- `TaskGet` は返された単一タスクを既知状態へ merge する。
- `deleted` は現在一覧から除外し、解釈不能な result は既知状態を変更しない。

result の JSON object、JSON 文字列、SDK content block 配列を小さな parser 境界で正規化し、元 payload は診断用に保持する。派生したタスクリストを別コレクションへ二重保存する案は、イベントとの整合性維持が増えるため採用しない。履歴再生コストは 1 run のイベント量に限定され、必要になった場合にだけ checkpoint イベントを追加できる。

### 5. ChatServiceが質問と表示状態の唯一のフロントエンド境界になる

共通層に `pending_questions(session_id, run_id)`、`answer_question(...)`、`interaction_state(events)` を追加する。`InteractionState` は pending 質問の順序付き一覧と最新 Task の辞書／表示順を含む。Streamlit と Slack Bot は SDK payload や Firestore 型を直接解釈しない。

Streamlit は履歴再生ごとに state を再構成し、pending 質問ごとに widget key へ質問 ID を含める。単一選択は radio、複数選択は multiselect、「その他」は明示選択時だけ text input を有効化する。送信は質問 ID 由来の session-state idempotency key を再利用し、受理後に再描画する。

Slack Bot はメッセージ受信時、スレッド対応済み session の active run と先頭 pending 質問を通常プロンプト開始より先に確認する。整数またはカンマ区切り整数だけの入力は番号として厳密に parse し、それ以外は自由入力とする。複数質問は ordinal 順に 1 問ずつ提示・回答し、全問回答後に SDK が再開する。質問を開始した application user と一致しない返信は拒否する。

### 6. Slack表示とTask表示は更新可能な要約にする

Streamlit はタスクを専用 status／一覧領域に Task ID 単位で描画する。Slack は run の作業中メッセージに「タスク」節を設け、最新 state から再生成して既存メッセージを rate limit 内で更新する。過去の TaskUpdate を追記し続けず、一般ツール活動とタスクリストを分ける。質問投稿は回答方法が変わらない限り同じ質問 ID について一度だけ行い、Bot 再起動時は永続状態から不足投稿を再作成できるよう handler を冪等にする。

## Risks / Trade-offs

- [回答待ちが Cloud Run Job の実行時間と費用を消費する] → max runtime を継続適用し、質問に回答期限を表示する。将来の suspend/resume は別変更とする。
- [SDK バージョン差で Task result の形や Task ID キーが変わる] → parser を隔離し、既知の複数形式を fixture test で固定し、未知形式では既存 state を維持する。
- [Firestore watch の切断で回答を見逃す] → watch 登録前後と再接続時に canonical record を再取得し、質問 ID と状態で重複適用を防ぐ。
- [回答とキャンセル／タイムアウトが競合する] → 同一トランザクションで run と質問状態を検証し、終端確定後の回答を拒否する。
- [Slack の `1` が通常プロンプトか回答か曖昧になる] → 同じスレッド・同じ所有者・active run に pending 質問がある場合だけ回答として解釈し、それ以外は通常プロンプトとする。
- [質問やTask入力に秘密情報が含まれる] → 既存イベントと同じ所有者境界・保持期限を適用し、ログには ID、型、長さのみを記録して回答本文や tool result を出力しない。

## Migration Plan

1. モデルと provider-neutral port を追加し、インメモリストアの契約テストを先に有効化する。
2. Firestore の質問サブコレクション、トランザクション、watch、TTL 設定を追加する。既存 session／run／event ドキュメントは変更せず、旧データは質問なしとして読めるようにする。
3. 制御クライアントと共通 ChatService の回答 API、イベント正規化、Task reducer を追加する。
4. Agent SDK アダプターを双方向クライアントへ切り替え、質問ブローカーと JobRunner の回答待ち heartbeat／timeout を接続する。
5. Streamlit と Slack Bot の UI、再接続、競合表示を有効化し、デモ手順を更新する。
6. 単体テスト、インメモリ end-to-end、Firestore codec／契約テストを通した後に配備する。

ロールバック時は先にフロントエンドの回答 UI を無効化し、旧 Job イメージへ戻す。追加した Firestore レコードは旧コードから参照されず、既存 run／event 契約を変更しないため残置できる。新 Job が質問待ち中の場合はキャンセルまたは期限到達を確認してから旧イメージへ戻し、実行中 SDK セッションを異なる実装で引き継がない。
