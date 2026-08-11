## Context

現在の Claude Agent 実行境界は `ResultMessage` の `total_cost_usd` と `duration_ms` を正規化し、run の `final` または `error` イベントへ保存している。一方、アプリケーション向けの composition API が受け取る callback は workspace 初期化・準備に限られ、終端実績を外部へ転送する境界はない。run、session、event の永続化 lifecycle は引き続きフレームワークが所有し、サンプル Agent に Store 操作を漏らさない必要がある。

## Goals / Non-Goals

**Goals:**

- アプリケーションが型付きの 1 レコードを受け取るだけで、ログ出力や BigQuery insert などへ実績を転送できる境界を設ける。
- 既に永続化されている SDK の料金・処理時間と、run・session の識別情報を単一の利用実績へまとめる。
- telemetry の障害を run の durable lifecycle から隔離し、hook 未指定の既存コードを互換に保つ。

**Non-Goals:**

- BigQuery の schema、client、認証、再試行キューをこのリポジトリへ追加しない。
- 利用実績の集計、請求、SDK 推定額の補正、または Cloud 実行基盤の費用計算は行わない。
- 外部送信先に対する exactly-once 配信を保証しない。

## Decisions

### 1. 不変の利用実績レコードと同期 hook を公開する

provider 非依存な runtime 型として frozen dataclass の `AgentUsageRecord` を追加し、`user_name: str`、`run_id: UUID`、`session_name: str`、`estimated_cost_usd: int | float | None`、`recorded_at: datetime`、`duration_ms: int | None` を持たせる。`UsageHook` はこのレコードを 1 引数で受け取る同期 callable とし、両方を package root から import 可能にする。

dict を直接渡す案は送信先ごとに型やキーの解釈がずれやすいため採用しない。非同期 hook との union は呼び出し規約と例外処理を複雑にするため、今回の公開契約は通常の Python 関数に限定する。非同期送信やバッファリングが必要な利用者は hook 内部で所有する。

### 2. composition API から実行境界まで hook を明示的に渡す

`GoogleCloudJobComposition.run_from_environment` に keyword-only の任意 `usage_hook` を追加し、`ClaudeAgentAdapter` の durable job 実行へ渡す。既定値は `None` とし、既存呼び出し元の挙動と source compatibility を維持する。

グローバル hook registry や環境変数による動的 import は、テスト分離を損ね、意図しないコード実行や構成不備を招くため採用しない。アプリケーションの composition root が Python callable を明示的に注入する。

### 3. 永続化済みの終端情報からレコードを組み立てる

run の terminal commit が成功した後、確定した run の `user_id`、`id`、`finished_at`、事前に取得した session の `title`、および同じ run の SDK 終端イベントに保存された `estimated_cost_usd` と `duration_ms` からレコードを作る。SDK 項目が存在しない場合は `None` のままとする。これにより hook と永続イベントが異なる料金値を報告することを避け、時刻は実績の確定時点として一意に定義できる。

hook を SDK message の受信直後に呼ぶ案は、後続の snapshot 保存や terminal commit が失敗した run を確定実績として通知してしまうため採用しない。所有権取得をスキップした Job と terminal commit に到達できなかった Job は通知対象外とする。

### 4. hook は best-effort とし lifecycle の結果を上書きしない

hook は terminal commit 後にプロセス内で 1 回呼び、例外は境界で捕捉して run ID 付きでログへ記録する。成功 run は終了コード 0、失敗・cancel・timeout run は既に決定した非 0 の終了コードをそのまま返す。外部送信の再試行、非同期化、重複排除が必要な場合は安定した run ID をキーとして hook 側で実装する。

hook 失敗を Job 失敗として再試行する案は、Agent 自体が成功した run を失敗へ反転させ、外部障害時に重複実行を増やすため採用しない。

### 5. サンプルは構造化された単一ログレコードを出力する

`example/agent` は module logger を用いる同期関数を定義し、利用実績の全フィールドを JSON 互換の単一レコードとして `INFO` 出力する。UUID と datetime は文字列化し、未取得の料金・処理時間は `null` として残す。サンプルの `run()` はこの関数を composition API へ渡すだけとし、将来は関数本体を BigQuery insert へ置き換えられる構造にする。

## Risks / Trade-offs

- [hook は terminal commit と同一トランザクションではなく、プロセス終了時には通知を失う可能性がある] → run ID と永続イベントを再送元にできる契約を保ち、厳密な配信が必要な実装では hook 側に再試行・照合を持たせる。
- [同期 hook が遅いと Job の終了が遅延する] → サンプルは軽量なログ出力に留め、外部送信実装には timeout やバッファリングを推奨する。run の状態は呼び出し前に終端済みとする。
- [user ID が email アドレスの場合、ログに個人情報が含まれる] → サンプルは要件どおり user ID を出力するが、ログ閲覧権限と保持期間はデプロイ側で制御し、外部 hook は送信先のデータ管理方針に従う。
- [hook の best-effort 動作では送信失敗を呼び出し元が終了コードで検知できない] → run の可用性を優先し、hook 失敗を明示的な error log と監視対象にする。

## Migration Plan

1. 公開 runtime 型と任意 hook 引数を後方互換な追加としてリリースする。
2. adapter の終端経路と hook の成功・失敗・未指定・claim skip をテストする。
3. サンプル Agent にログ hook を登録し、1 run の終端につき単一レコードが出ることを確認する。
4. 問題がある場合はサンプルから hook 登録を外すだけで従来動作へ戻せる。データ migration や永続 schema の rollback は不要である。
