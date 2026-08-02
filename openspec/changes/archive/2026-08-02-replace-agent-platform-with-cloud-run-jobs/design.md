## Context

変更理由は [proposal.md](./proposal.md) を参照する。現行実装は Agent Platform の Session、Event、Long-running Operation、BYOC runtime contract を中心に組み立てられており、84 タスク中 25 タスクが完了している。snapshot の安全な作成・復元、Protocol と in-memory fake、再試行、構造化ログなどは再利用できる一方、Session Store、Operation client、runtime API、Gateway は新方式と責務が合わない。

Cloud Run Jobs のコンテナはリクエスト待受サーバーではなく、Execution ごとに起動して処理完了後に終了する。したがってチャット UI とジョブが同じ HTTP 接続を共有せず、永続ストアを制御面とデータ面の受け渡しに使う必要がある。Firestore はトランザクション、順序付き query、リアルタイム listener を提供し、この役割に適する。大きな workspace と Claude transcript は Firestore の document size や更新頻度に適さないため、オブジェクトストレージに分離する。

Cloud Run Jobs、Firestore、GCS は初期実装であり、将来 Kubernetes Job、別 database、別 object store へ交換する可能性がある。ただし交換可能性のために各プロバイダーの最小公倍数へ機能を落とすのではなく、アプリケーションが必要とする意味論を port として定義し、adapter がそれを満たす。

## Goals / Non-Goals

**Goals:**

- UI 接続から独立して 30 分程度以上の run を実行し、途中イベントと終端状態を再接続後も取得できるようにする。
- Firestore を会話、run、イベント、active-run 制約の正本とし、リアルタイム listener とユーザー別セッション一覧を提供する。
- 同じ run のディスパッチが重複しても Claude Agent SDK の論理実行を 1 件に制限する。
- 実行、チャット永続化、workspace 永続化の各境界を Protocol とし、provider adapter と in-memory fake を交換可能にする。
- 現行実装から provider 非依存で安全性が確認済みのコードを移行し、Agent Platform 固有コードを明示的に廃止する。

**Non-Goals:**

- 任意の Kubernetes クラスタへ実際にデプロイする adapter を今回提供すること。
- Firestore 以外の本番 database adapter、または GCS 以外の本番 object store adapter を今回提供すること。
- ブラウザから Firestore へ直接接続するための Firebase Authentication と Security Rules を構築すること。
- エージェントのプロンプト、ツール、業務ロジック、Secret Manager、IAP をフレームワークが管理すること。
- workspace ディレクトリ分離をコンテナまたは OS レベルのセキュリティ境界にすること。
- token delta を無制限に永続化すること、または実行済み Claude Agent SDK 処理を完全な exactly-once にすること。

## Decisions

### 1. 制御面、ジョブ実行面、永続化面を分離する

主要コンポーネントを次の責務へ分ける。

```text
Streamlit / control client
  ├─ ChatStore: session作成・一覧、run予約、event購読
  └─ ExecutionBackend: job開始・状態取得・cancel

Cloud Run Job container
  └─ JobRunner
      ├─ ChatStore: run取得・claim・event/state追記
      ├─ WorkspaceStore: snapshot復元・commit
      └─ AgentRunner: Claude Agent SDK実行
```

UI はメッセージと run を永続化してから Execution を開始する。ジョブへは run ID だけを渡し、入力メッセージや秘密情報は渡さない。ジョブは run ID から必要なデータを取得するため、UI のプロセスや接続が消えても実行できる。

代替案の Cloud Run Service や Pub/Sub worker は、常駐サービスまたは追加キューを必要とする。今回の処理単位と運用要件には 1 run 1 Execution が明瞭なため Cloud Run Jobs を採用する。将来キューが必要になった場合も制御クライアントと `ExecutionBackend` の間へ追加できる。

### 2. 3種類のprovider portを公開境界にする

内部 port は provider SDK の型を返さず、ドメインモデルだけを扱う。

- `ExecutionBackend`: `start(run_id)`、`get(execution_ref)`、`cancel(execution_ref)`。Cloud Run Jobs adapter を初期実装とし、Kubernetes adapter は同じ状態モデルを実装する。
- `ChatStore`: session create/get/list、run reserve/get/claim、event append/list/subscribe、cancel request、terminal commit、reconciliation lease。
- `WorkspaceStore`: immutable snapshot create/get/delete、条件付き作成、object version と hash の検証。
- `AgentRunner`: 初回 query と Claude session resume、SDK event の正規化、協調停止。

`subscribe` は callback または async iterator の共通イベント列として公開する。Firestore listener の thread/callback、Kubernetes watch、テスト fake の実装詳細は adapter 内へ閉じ込める。別 ChatStore が push 購読を提供しない場合は、その adapter 自身が cursor polling を同じ契約へ変換する。

既存の `SessionsApi`、`EventsApi`、`Operations` は新 port へ移行し、Agent Platform resource name や Operation 型を公開モデルから除去する。

### 3. Firestoreのユーザー配下にセッション、run、eventを配置する

論理データモデルは次のとおりとする。外部 user ID は正規化後に SHA-256 で `user_key` へ変換し、未検証値を document path に使わない。サービス側で認証済み identity と要求 user ID を対応付ける責務は UI/BFF 境界に置く。

```text
users/{user_key}
  sessions/{session_id}
    {title, created_at, updated_at, active_run_id, latest_run_state,
     latest_event_sequence, schema_version}
    runs/{run_id}
      {idempotency_key, state, input_event_id, execution_ref,
       execution_owner, claim_expires_at, cancel_requested_at,
       claude_session_id, workspace_id, snapshot_ref,
       result, error, created_at, started_at, finished_at, version}
      events/{event_id}
        {sequence, event_type, occurred_at, payload, schema_version}
```

セッション一覧は user subcollection を `updated_at DESC, session_id DESC` で query し、最後の組を cursor とする。run イベントは `sequence ASC, event_id ASC` で query・購読する。必要な複合 index は Terraform に含める。

イベント payload は会話再表示に必要な正規化済み情報に限定する。大きな tool 入出力やバイナリは object store の参照と hash を保存し、Firestore の document size 上限へ近づけない。raw prompt/tool payload は既定 INFO ログへ出さず、DEBUG は利用者が明示的に有効化する。

### 4. run予約をFirestore transactionで原子的に行う

新規 run は次の単一 transaction で予約する。

1. session と同じ `idempotency_key` の run を読む。
2. 同じキーがあれば既存 run を返す。
3. `active_run_id` が非空なら競合として既存 run を返す。
4. user message event、`requested` run、session の `active_run_id` と `updated_at` を書く。

Firestore transaction の再実行を考慮し、run ID と event ID は transaction 外で発行し、同じ入力を再利用する。開始 API が transaction 成功後に停止した場合、run は `requested` のまま残る。制御クライアントの再送または reconciler が同じ run をディスパッチできる。

GCS 条件付き object を active lock としていた旧設計より、run registry と排他の更新を同じ transaction で確定できる。別 ChatStore adapter も compare-and-set または transaction により同じ意味論を提供する。

### 5. ディスパッチはat-least-once、エージェント実行はsingle-winner claimにする

Cloud Run Execute API と Firestore を横断する transaction は存在しない。Execution 作成直後に制御プロセスが停止すると、`execution_ref` を保存できず同じ run を再ディスパッチする可能性がある。このため外部 Execution の exactly-once 作成を前提にしない。

各ジョブコンテナは起動直後、実行 backend が提供する一意な実行 identity と run ID を使い、Firestore transaction で `execution_owner` を claim する。最初の有効な owner だけが `running` へ遷移して Agent を実行する。別 owner は duplicate event を冪等に残すか、何も変更せず成功終了する。claim は heartbeat と lease を持つが、期限切れだけを理由に別 owner が自動的に Claude 実行を再開してはならない。backend が元 Execution の終端または消失を確認した場合だけ、管理操作により再ディスパッチ可能にする。

Cloud Run Jobs の task retry は既定 0 とする。インフラ障害で再試行を有効にしても claim により同時実行を防ぐ。これにより「外部ジョブは at-least-once、Claude 実行は同時に最大 1 件」という現実的な保証を明文化する。

### 6. Firestore run状態を正本にし、backend状態をreconcileする

run 状態は次の state machine を使う。

```text
requested -> dispatching -> pending -> running
     |            |           |          |
     v            v           v          v
dispatch_failed  failed      cancelled  completed
                                         failed
                                         cancelled
                                         timed_out

active状態: requested, dispatching, pending, running, cancel_requested
終端状態: dispatch_failed, completed, failed, cancelled, timed_out
```

UI は Firestore run を即時表示し、必要に応じて `ExecutionBackend.get` の結果で補正する。backend が failed/cancelled なのに Firestore が active の場合、reconciler は provider 状態と時刻を event として保存して終端へ移す。backend が succeeded でも snapshot 参照と final event がなければ completed にせず `persistence_failed` として failed にする。

reconciliation は session/run ごとの短い lease を ChatStore から取得して 1 worker だけが行う。UI の status 取得時に軽量 reconcile できるほか、将来 Cloud Scheduler 等から同じサービスを呼べるようにするが、定期 scheduler の構築は初期範囲に必須としない。

### 7. event IDとsequenceでリアルタイム配信を冪等にする

run document の `next_sequence` を transaction 内で増加させ、event document を同じ transaction で作成する。SDK event から安定した event ID を生成できない場合は、run owner とローカル event counter から決定的に生成する。保存再試行は同じ event ID を使う。

Firestore listener は初回 snapshot、再配信、順序変更を起こし得るため、UI は `(sequence, event_id)` で整列し、event ID で重複を除く。再接続時は最後に確定表示した sequence と event ID を cursor として過去 query を行い、その後 listener を開始する。query と listener の境界で重複しても重複除去でき、イベントをメモリだけに置かないため欠落を回復できる。

token ごとの delta は書込回数とコストを増やすため、短い時間窓または意味のある SDK message 単位でまとめる。正確な batching 値は設定とし、会話順序と主要進捗を失わない。

### 8. snapshot commitをrun完了の前提にする

workspace snapshot の安全性は旧実装を移行する。論理構造は `manifest.json`、`workspace/`、`claude-session/` とし、run 固有キーへ条件付き作成する。`WorkspaceStore` は provider 非依存の `object_key`、`version`、`sha256`、`size` を返し、GCS adapter が generation precondition へ変換する。

正常終了は次の順序で確定する。

1. Agent の final response をイベントとして永続化する。
2. transcript と workspace の snapshot を条件付き保存する。
3. ChatStore transaction で snapshot 参照、Claude session ID、結果、completed event を保存し、session の active run を解除する。
4. UI は completed を受信して最終表示を確定する。

3 が失敗した場合、同じ run owner は既存 snapshot の hash/version を検証して再試行する。未commit snapshot は復元対象にせず、既定の猶予後に GC する。failed、cancelled、timed_out では変更中 workspace を snapshot として commit しない。

### 9. キャンセルはFirestoreとExecutionBackendの両方へ伝える

制御クライアントは ChatStore transaction で `cancel_requested` を記録してから backend の cancel を呼ぶ。ジョブは起動時、SDK event ごと、heartbeat 時に cancel flag を読み、AgentRunner へ協調停止を伝える。Cloud Run Execution の強制停止が先に完了してジョブが状態を書けない場合、status reconciliation が cancelled を確定して active run を解除する。

キャンセル API の一時失敗時も cancel flag は残るため、UI は停止完了と誤表示しない。終端 run へのキャンセルは元状態を保つ。cancel_requested の間は次の run を開始しない。

### 10. 設定と依存注入でproviderを選択する

公開組み立て API は、`ExecutionBackend`、`ChatStore`、`WorkspaceStore`、`AgentRunner`、clock、設定を明示的に受け取る。Google Cloud 用 factory は project、region、Firestore database、GCS bucket、Cloud Run Job name から各 adapter を構築する。コア lifecycle は Google SDK を import しない。

リリース YAML は version 付きで、次を管理する。

- Google Cloud project、region、Artifact Registry、image
- Cloud Run Job name、CPU、memory、task timeout、task retry、service account
- Firestore database、location、collection schema version、保持期間
- GCS bucket、snapshot prefix、容量・保持期間
- run 最大時間、idle timeout、event batching、retry、log level

秘密値用 field と未知の field は拒否する。Terraform とデプロイスクリプトは同じ正規化済み設定を使い、plan 前に対象 resource と実効値を表示する。

### 11. IAMを制御主体とジョブ主体で最小化する

制御主体は対象 Cloud Run Job の実行・Execution 取得・キャンセル、および対象 Firestore データの read/write に必要な権限を持つ。ジョブ主体は Cloud Run Job を起動する権限を持たず、対象 Firestore 名前空間と GCS bucket object、Claude on Vertex 等エージェント自身が必要とする API だけへアクセスする。

サービスアカウント key は発行せず Application Default Credentials または Google 実行 identity を使う。Firestore をブラウザへ直接公開しないため、サンプル UI の server process が制御主体として listener を保持する。本番の user 認証と user ID 解決は IAP 等の BFF 境界へ差し替える。

### 12. 旧実装は分類して移行する

旧 change のコードを次の 3 群へ分類する。

- 移行: 公開 error、retry、logging、Agent adapter の provider 非依存部、安全な archive、snapshot manifest、temporary directory、in-memory fake の一般化可能な部分。
- 改修: models、protocols、lifecycle、client、workspace store。Operation を execution reference、Session Event を ChatStore event、GCS lock を transaction/claim へ置換する。
- 廃止: Agent Platform runtime server、Sessions SDK adapter、Long-running Operation adapter、Agent Gateway/Registry、Agent Engine deploy、mirror/transcript 比較の Agent Platform 側。

完了済みタスクを機械的に引き継がず、新 tasks で「再利用可能性をテストで確認して移行」として追跡する。これにより旧前提を無意識に残すことを防ぐ。

## Risks / Trade-offs

- [Firestore と Cloud Run Execute API を横断する transaction がない] → at-least-once dispatch と single-winner claim を採用し、requested run と孤立 Execution を reconcile する。
- [Firestore listener は再配信・順序変更・一時切断を起こす] → 永続 sequence、event ID、cursor query、クライアント側重複除去を必須にする。
- [イベント頻度が高いと Firestore コストと document contention が増える] → event を subcollection に分散し、token delta を時間窓でまとめ、payload 上限を設ける。
- [Cloud Run Job の強制停止時はジョブ自身が終端状態を書けない] → backend 状態を正本 run へ反映する reconciliation 経路を用意する。
- [lease 切れだけで再実行すると二重 Claude 実行になり得る] → 時刻だけでは owner を奪わず、元 Execution の終端確認を再 claim 条件にする。
- [抽象化が provider 固有機能を隠しすぎる] → domain が必要とする transaction、subscription、conditional create を port の必須意味論とし、満たせない adapter は構成時に拒否する。
- [Firestore database の location は後から変更できない] → Terraform apply 前に project、database、location を明示し、既存 database との不一致を検証する。
- [旧 Agent Platform コードを残すと二つの方式が混在する] → 移行完了時に公開 export、依存、ドキュメント、テストから Agent Platform 経路を削除する。

## Migration Plan

1. 新 port と provider 非依存モデルを追加し、in-memory adapter の契約テストを先に通す。
2. 現行の snapshot、安全な archive、retry、logging を新契約へ移行し、既存テストを適応する。
3. Firestore ChatStore と Cloud Run Jobs ExecutionBackend を実サービス contract test で固定する。
4. 新 JobRunner と control client を実装し、重複 dispatch、再接続、cancel、snapshot 部分失敗を fake で統合検証する。
5. Terraform とリリース設定を Cloud Run Jobs、Firestore、GCS 用へ置換する。
6. サンプルと README を新経路へ切り替え、Google Cloud opt-in test で end-to-end を確認する。
7. Agent Platform 固有 export、依存、deploy、テストを削除し、旧方式を利用できないことを migration note に記載する。
8. `add-gemini-enterprise-hosting-adapter` は main specs へ同期せず「検証により中止した変更」として archive し、本 change と git 履歴から経緯を参照できるようにする。

ロールバック時は直前の git revision と既存クラウドリソースへ戻す。Firestore schema と新 GCS prefix は旧 Agent Platform リソースから分離し、移行中に旧データを破壊しない。旧方式から Firestore への Session データ自動移行は行わない。
