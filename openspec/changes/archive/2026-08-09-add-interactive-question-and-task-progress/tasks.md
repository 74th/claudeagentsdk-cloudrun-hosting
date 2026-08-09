## 1. 質問モデルとストア契約

- [x] 1.1 質問、選択肢、回答、pending／answered 状態、ordinal、冪等性情報、保持期限を表す provider-neutral モデルと入力検証を追加する
- [x] 1.2 ChatStore に質問の冪等作成、所有者付き取得・回答、ジョブ用取得・回答通知購読の契約を追加し、競合と受付終了を表す安全なエラーを定義する
- [x] 1.3 質問モデルの Firestore codec と、旧データを質問なしとして読める後方互換テストを追加する

## 2. 質問状態の永続化

- [x] 2.1 インメモリストアへ質問作成、一覧、原子的な初回回答、冪等再送、回答通知を実装する
- [x] 2.2 Firestore ストアへ run 所有者・active 状態・質問状態を同時検証する回答トランザクションと質問取得を実装する
- [x] 2.3 Firestore の回答 watch、切断後の再取得、run と同じ期限切れ除外および TTL 設定を実装する
- [x] 2.4 両ストアについて競合回答、終端 run、他ユーザー、期限切れ、通知再配信の契約テストを追加する

## 3. 制御APIと共通対話状態

- [x] 3.1 ControlClient に所有者境界と冪等性を維持する質問一覧・回答 API を追加する
- [x] 3.2 共通イベント表現へ question_pending／question_answered を追加し、質問 payload を検証して未知イベント互換性を維持する
- [x] 3.3 TaskCreate／TaskUpdate／TaskGet／TaskList の tool use と result を tool ID で相関し、複数の result 形式と Task ID キーを扱う Task reducer を実装する
- [x] 3.4 ChatService に pending 質問、回答送信、質問と最新タスクリストを返す InteractionState API を追加する
- [x] 3.5 TaskList 後の更新、TaskCreate の結果 ID、deleted、壊れた result、質問の履歴復元、回答競合について共通層の単体テストを追加する

## 4. Agent SDKとJobの回答待ち

- [x] 4.1 ClaudeAgentAdapter を継続ストリーム対応の ClaudeSDKClient へ移行し、既存 resume、session store、permission 方針を維持して can_use_tool を接続する
- [x] 4.2 AskUserQuestion の 1〜4 問と各 2〜4 選択肢を検証し、決定論的 ID で永続化して全回答を PermissionResultAllow の answers へ変換する質問ブローカーを実装する
- [x] 4.3 回答待ち中の heartbeat、Firestore watch 再接続、キャンセル、SIGTERM、max runtime を JobRunner と接続し、pending 中だけ通常 idle timeout を停止する
- [x] 4.4 Agent Adapter が Task 系を含む tool use／tool result の ID、name、input、result、error を欠落なく順序付きイベントへ保存できるよう正規化を更新する
- [x] 4.5 SDK の質問再開、複数問、自由入力、回答待ちキャンセル／timeout、Task result 相関を fake client とインメモリストアでテストする

## 5. Streamlitの質問UIとタスク表示

- [x] 5.1 Streamlit の ViewModel から共通 InteractionState の取得と冪等な質問回答を利用できるようにする
- [x] 5.2 質問 ID ごとの単一選択、複数選択、「その他」自由入力、空入力検証、回答送信中／競合表示を実装する
- [x] 5.3 Task ID 単位で pending／in_progress／completed と依存関係を更新表示し、再訪時に pending 質問と最新タスクを復元する
- [x] 5.4 Streamlit の選択、複数選択、自由入力、重複送信防止、削除タスク、再接続をテストする

## 6. Slack Botの質問回答とタスク表示

- [x] 6.1 Slack メッセージ処理で通常 run 開始前に同じスレッドの active run と pending 質問を照合し、元の application user だけの回答を受け付ける
- [x] 6.2 番号付き質問の整形と、`1`、`1,3`、自由入力を厳密に解釈する parser、範囲外／単一選択への複数番号の再案内を実装する
- [x] 6.3 複数質問を ordinal 順に提示し、最新タスクリストを一般ツール履歴と分けて rate limit 内で更新し、Bot 再起動後も永続状態から復元する
- [x] 6.4 Slack の番号回答、自由入力、別ユーザー拒否、通常プロンプトとの区別、重複イベント、タスク更新、再起動をテストする

## 7. 統合確認とドキュメント

- [x] 7.1 インメモリ end-to-end テストで質問イベント、UI 回答、SDK 再開、Task 状態遷移、終端までを検証する
- [x] 7.2 README に Streamlit と Slack Bot で選択、複数選択、「その他」、番号回答、タスク進捗を確認するデモ手順と回答期限の注意を追記する
- [x] 7.3 全テスト、静的検査、Terraform 検証、`openspec validate --strict` を実行し、既存 run／イベント利用の回帰がないことを確認する
