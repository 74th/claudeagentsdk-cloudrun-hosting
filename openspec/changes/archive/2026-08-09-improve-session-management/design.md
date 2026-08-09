## Context

現在は UI の New session 操作が直ちに `create_session` を呼ぶため、プロンプト未送信でも Firestore に空セッションが残る。`Session.title` と更新日時順の一覧契約はすでにあるが、UI は空タイトルを ID で代替し、選択セッションでは最新 run のイベントだけを表示する。また、`Run.execution.name` に Cloud Run 実行 ID は保存されるが UI はアプリケーションの run ID しか表示していない。

Firestore は `users/{user}/sessions/{session}/runs/{run}/events/{event}` の階層であり、親ドキュメントの削除は子コレクションを連鎖削除しない。リリース設定には既定 30 日の `run_retention_days` があるものの、現在は Firestore ドキュメントの期限にも Terraform の TTL field policy にも接続されていない。詳細な要求は delta specs を参照する。

## Goals / Non-Goals

**Goals:**

- 初回送信を再試行しても、名前付きセッション、最初の run、ユーザーイベントが重複しないトランザクション境界を定義する。
- ChatStore の provider 非依存性を保ったまま、複数 run の履歴を安定した順序で取得する。
- Firestore TTL の非同期削除と非連鎖削除を考慮し、30 日経過後の非表示と最終的な物理削除を両立する。
- 既存の空タイトル・期限なしドキュメントを読み取れる段階的な移行とロールバック境界を定義する。

**Non-Goals:**

- 利用者によるセッション名の編集、検索、削除 UI は追加しない。
- Claude の推論をセッション名生成のためだけに追加実行しない。
- GCS snapshot の既存 lifecycle や Claude transcript の復元方式は変更しない。
- 30 日を超える監査アーカイブや削除済み Firestore データの復元機能は提供しない。

## Decisions

### 1. UI は永続セッションではなく draft 状態を持つ

New session は Streamlit の session state を `draft` に切り替えるだけとし、ID を生成・表示せず空のチャット入力を描画する。最初の非空プロンプトを受けたとき、UI は冪等キーを session state に保持して新しい ControlClient 操作へ渡し、永続化と dispatch が成功または確定失敗するまで同じキーを再利用する。開始結果が返った後にだけ選択中 session ID と一覧を更新する。

既存セッションを先に作って初回 run で更新する案は現在の空セッション問題を残すため採用しない。draft 用の一時 ID を割り当てる案も、利用者が永続 ID と誤認しやすく再試行状態が増えるため採用しない。

### 2. 初回セッションと run を一つの ChatStore トランザクションで予約する

provider-neutral な開始結果モデル（session と run を含む）と、初回用の ChatStore 操作を追加する。ControlClient は最初のプロンプトから session、workspace、run、user event を組み立て、Firestore 実装は同一トランザクションで全ドキュメントを作成する。その後の Cloud Run dispatch、execution 保存、dispatch failure の扱いは既存 `reserve_and_start` と同じ状態遷移を再利用する。dispatch が失敗しても、ユーザー入力を持つ failed run とセッションは診断・再訪可能なので削除しない。

トランザクション再試行で別セッションが作られないよう、session ID、workspace ID、run ID は user ID とクライアント生成の高エントロピー冪等キーから用途別 namespace で決定的に導出する。UI は初回開始が確定するまでキーを保持する。Firestore 全体を idempotency key で検索する案は競合時の一意性を保証しづらく、専用 coordinator collection を追加する案は TTL と運用対象を増やすため採用しない。既存の `create_session` と後続 run 用 `reserve_run` は互換性のため残すが、サンプル UI の新規会話では使用しない。

### 3. セッション名は最初のプロンプトから決定的に生成する

現在の Claude SDK 連携が永続化しているのは Claude session ID であり、独立した表示名ではない。このため追加のモデル呼び出しには依存せず、最初のプロンプトの先頭の非空行から前後空白と連続空白を正規化し、Unicode の先頭 80 文字を表示名とする。後続 run は名前を自動変更しない。既存の空タイトルは UI で `Untitled session` と表示し、一覧ラベルに ID を代替利用しない。

Claude に要約タイトルを生成させる案は、開始前には結果がなく UI 更新が遅れること、追加コストと失敗経路が生じることから採用しない。将来の明示的 rename はこの安定名を上書きする別変更として追加できる。

### 4. 履歴は run のページングと既存イベント順序を組み合わせて復元する

ChatStore/ControlClient に、所有権を検証しながら session 内の run を `(created_at, id)` の昇順で安定ページングする操作を追加する。UI は選択セッションの run ページを順に読み、各 run の既存 `list_events` 結果を `(sequence, event_id)` 順に連結して会話を描画する。active run があれば永続イベント表示後に既存の増分購読へ接続する。Firestore には run 履歴順序用 index を追加し、インメモリ実装にも同じカーソル契約を実装する。

Session に全イベントを埋め込む案は Firestore のドキュメント上限と書き込み競合を招くため採用しない。最新 run だけをセッションへ複製する案は完全復元要件を満たさない。

### 5. TTL メタデータは保存アダプタで管理し、読み取り時にも期限を強制する

公開ドメインモデルへ Firestore 固有の TTL 設定を漏らさないため、codec/Firestore adapter が全 session、run、event payload に共通 Timestamp field `expires_at` を付加し、decode 前に除去する。`run_retention_days` は既存リリースファイルとの互換性を保って Firestore チャット階層全体の保持日数として使用し、Control plane と Job の両方へ同じ値を渡す。新規作成と各更新では、その書き込み時刻から保持日数後へ `expires_at` を設定する。変更されない event は `occurred_at` を基準に 30 日保持する。

Terraform は指定された名前付き database の `sessions`、`runs`、`events` 各 collection group の `expires_at` field に TTL policy を設定する。これは親削除が子を削除しない Firestore の性質に対応する。TTL 削除は期限到達と同時ではないため、get/list/history は `expires_at <= now` を返さない。更新日時順ページングは維持し、期限切れ項目を読み取り側で除外しながらページを追加走査し、カーソルは最後に走査したドキュメントを指す。これにより TTL の削除遅延中も表示期間を超えず、並び順も変わらない。

セッション親だけへ TTL を設定する案は orphan subcollection を残すため採用しない。期限条件を Firestore query の先頭に置く案は一覧の主順序と cursor/index 契約を複雑にするため採用しない。

### 6. UI は保存済み execution reference を識別子別に表示する

選択セッションの見出しにセッション名を置き、識別子領域では `Session ID` と `Cloud Run execution ID`（`Run.execution.name`）を隣接表示する。`Run ID` は別ラベルで表示し、execution が未保存なら `pending` とする。セッション一覧は UTC の timezone-aware な最終更新時刻と名前だけを表示し、順序はサーバーから返る降順を保持する。

## Risks / Trade-offs

- [TTL は期限到達後すぐに物理削除されない] → 読み取り側でも期限を検査して非表示を保証し、物理削除は Firestore の TTL SLA に委ねる。
- [親より子が長く残る期間がある] → session、run、event の全 collection group に TTL を設定し、残留を有限にする。
- [既存ドキュメントには `expires_at` がない] → reader は欠落を一時的に許容し、基準 timestamp から期限を計算する冪等なバックフィルを小さい batch で実行する。新しい writer は更新時にも補完する。
- [バックフィル直後に古いデータが削除対象になる] → 適用前に Firestore export を取得し、dry-run で件数と最古日付を提示してから書き込む。
- [履歴が多いセッションでは読み取り回数が増える] → run をページングし、30 日 TTL で上限を設ける。UI は表示可能なイベント種別だけを描画する。
- [決定的 ID が推測可能になる] → 入力に UI が生成する高エントロピー UUID 冪等キーを含め、外部 user ID や prompt を ID へ直接埋め込まない。所有権検証は従来どおり必須とする。
- [古い空タイトルが名前要件を満たさない] → UI のみ `Untitled session` を使用し、新規セッションは常に非空名を保存する。

## Migration Plan

1. 互換 reader、初回開始 API、履歴 API、`expires_at` writer、および新旧 title の UI fallback を先に配備する。この段階では TTL policy を有効化しない。
2. Terraform plan で 3 collection group の TTL policy と履歴 index が指定 database のみに作成されることを確認し、適用する。
3. 既存 session、run、event を collection group ごとに走査するバックフィルを dry-run し、基準 timestamp、期限、対象件数を確認する。Firestore export 後、冪等 batch で `expires_at` を補完する。
4. 新 UI を配備し、空 draft、初回再試行、一覧順序、複数 run 復元、active run 再訪、ID 表示を live database で確認する。
5. ロールバック時は UI と API を旧版へ戻せるが、TTL により削除済みのデータはアプリケーションロールバックでは復元できない。必要な場合は事前 export から別 database へ復元し、検証後に切り替える。
