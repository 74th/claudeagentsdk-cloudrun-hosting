## 1. 既存実装の移行監査と外部契約検証

- [ ] 1.1 現行コードと完了済み旧タスクを、provider非依存で再利用、契約変更して改修、Agent Platform固有で廃止の3群へ分類して移行表を残す
- [ ] 1.2 Cloud Run Jobs SDK/APIでExecution開始、run IDの引渡し、状態取得、cancel、終了状態、実行identity、task timeout、task retryの実request/responseを記録する
- [ ] 1.3 Execution作成直後の制御プロセス停止、同一runの重複ディスパッチ、cancel中の状態遷移、SIGTERM猶予を実測してcontract fixtureを残す
- [ ] 1.4 Firestore emulatorまたは検証環境でtransaction再実行、条件付きrun claim、event追記、複合cursor pagination、snapshot listenerの初回配信・再配信・切断復帰を確認する
- [ ] 1.5 Firestoreのdocument size・transaction・write contention・listener制約に収まるevent payloadとbatching既定値を決めてfixtureへ固定する
- [ ] 1.6 GCS条件付き作成、generation取得、整合性検証、lifecycle ruleの契約を既存実装と選定SDKで再確認する
- [ ] 1.7 PoC結果からGoogle Cloud SDK依存version、使用method、状態mapping、retry対象、構成上限を確定し、再実行可能なopt-in検証手順を残す

## 2. provider非依存モデルとportへの移行

- [ ] 2.1 `pyproject.toml` からAgent Platform専用依存を除き、固定したFirestore・Cloud Run Jobs依存を追加してlockfileを更新する
- [ ] 2.2 user、session、run、execution reference、Claude session、workspace、event cursorを分離したprovider非依存モデルへ置換する
- [ ] 2.3 requestedから各終端状態までのrun state machine、active判定、許可遷移、versionをモデル化して単体テストする
- [ ] 2.4 `ExecutionBackend` のstart・get・cancel Protocolと正規化済み状態・エラーを定義し、in-memory fakeを実装する
- [ ] 2.5 `ChatStore` のsession、run reserve/claim、event append/list/subscribe、cancel、terminal commit、reconcile lease Protocolを定義する
- [ ] 2.6 `WorkspaceStore` の条件付きsnapshot作成、取得、検証、削除 ProtocolへGCS固有型を漏らさない参照モデルを定義する
- [ ] 2.7 agent instance/factory、clock、identity providerを含む組み立て境界とGoogle Cloud用factoryを定義する
- [ ] 2.8 retry、構造化logging、公開errorの再利用部分を新しいuser/session/run/execution fieldとerror codeへ適応する
- [ ] 2.9 provider SDK型がコアmodels・lifecycle・clientの公開signatureへ漏れていないことをtype testで検証する

## 3. Firestore ChatStore

- [ ] 3.1 user IDの正規化・hash化とusers/sessions/runs/eventsのversion付きdocument codecを実装する
- [ ] 3.2 sessionの作成・取得・所有者検証と、title・最新run状態・updated timestampの更新を実装する
- [ ] 3.3 `(updated_at, session_id)` cursorによるユーザー別session一覧の降順paginationを実装する
- [ ] 3.4 idempotency key確認、active run排他、user message event、run、session更新を単一transactionで行うrun予約を実装する
- [ ] 3.5 execution identityによるsingle-winner run claim、heartbeat、owner検証をtransactionで実装する
- [ ] 3.6 event ID重複除去、単調増加sequence割当、event document作成をtransactionで行う追記処理を実装する
- [ ] 3.7 cursor以後のevent queryとFirestore snapshot listenerを共通購読契約へ変換し、終了時にlistenerを確実に解除する
- [ ] 3.8 cancel_requestedの冪等更新と、ownerだけが行えるterminal commit・active run解除をtransactionで実装する
- [ ] 3.9 session/run単位のreconciliation lease取得・更新・解放を実装する
- [ ] 3.10 emulatorを使い所有者分離、pagination、transaction競合・再実行、重複event、listener再配信・再接続、部分障害をテストする
- [ ] 3.11 Firestoreなしで同じ契約を検証できるthread-safeなin-memory ChatStoreと共通contract test suiteを実装する

## 4. Cloud Run Jobs ExecutionBackend

- [ ] 4.1 固定SDKを薄く包むCloud Run Jobs client adapterを実装し、project・region・job resource nameを検証する
- [ ] 4.2 run IDと必要最小限の非秘密設定だけをoverrideしてExecutionを開始し、正規化したexecution referenceを返す
- [ ] 4.3 Cloud Run Executionのconditionをpending・running・succeeded・failed・cancelledへ決定的に正規化する
- [ ] 4.4 実行中Executionへのcancelと終端Executionへの冪等cancelを実装する
- [ ] 4.5 API一時障害だけを再試行し、not-found、権限、quota、入力、region不一致を安定した公開errorへ変換する
- [ ] 4.6 同じrunに既存execution referenceがある場合は再作成せず、参照保存前の停止では重複dispatchを許容して診断eventを残す制御を実装する
- [ ] 4.7 fake clientで開始、状態mapping、cancel、timeout、権限、一時障害、重複要求を網羅する単体テストを追加する

## 5. WorkspaceStoreとAgent JobRunner

- [ ] 5.1 既存の安全なarchive作成・展開、一時directory、容量検査、manifest処理をprovider非依存WorkspaceStoreへ移行する
- [ ] 5.2 GCS adapterでrun固有snapshotの条件付き作成、generationとhashを含む参照、検証付き取得、削除を実装する
- [ ] 5.3 committed snapshotだけを復元し、未commit・破損・schema/SDK非互換をエージェント実行前に拒否する
- [ ] 5.4 snapshotがない場合だけworkspace initializerを実行し、失敗時にrunを開始しない処理を移行する
- [ ] 5.5 Claude Agent SDKのtranscript保存先をjob固有directoryへ分離し、初回queryと保存済みClaude session resumeを統一する
- [ ] 5.6 SDK messageをuser・agent・tool started/completed・progress・error・final eventへ正規化し、安定event IDを生成する
- [ ] 5.7 JobRunnerの起動引数と非秘密設定を検証し、Firestore run取得・cancel確認・single-winner claim後だけ実行するentrypointを実装する
- [ ] 5.8 Agent eventをbatching方針に従って実行中に逐次保存し、heartbeatとcancel flag確認を行うexecute処理を実装する
- [ ] 5.9 最大実行時間とidle timeoutを個別に監視し、協調停止後にtimed_outを保存する
- [ ] 5.10 正常終了時にfinal event、snapshot、terminal transactionの順でcommitし、途中失敗時に同じrunで確定処理を再開できるようにする
- [ ] 5.11 failed・cancelled・timed_out・duplicate・SIGTERMの各経路で未commit変更を破棄し一時directoryを削除する
- [ ] 5.12 path安全性、容量、条件付き保存、resume、重複job、cancel、timeout、SIGTERM、snapshot/Firestore部分失敗を単体・統合テストする

## 6. 制御クライアントと状態reconciliation

- [ ] 6.1 control clientにsession作成・取得・一覧、run予約、Execution開始、event購読、status、cancelの高水準APIを実装する
- [ ] 6.2 run予約後にdispatchingへ遷移し、Execution開始とexecution reference保存を行い、開始失敗時にdispatch_failedとactive run解除を確定する
- [ ] 6.3 同じidempotency keyの再送で既存run・execution referenceを返し、requestedのまま残ったrunだけを安全に再dispatchする
- [ ] 6.4 Firestore run状態とExecutionBackend状態を照合し、backend終端・実行消失・snapshot欠落を補正するreconcilerを実装する
- [ ] 6.5 cancel_requested保存後にbackend cancelを呼び、停止確認後だけcancelledとactive run解除を確定する
- [ ] 6.6 event queryとlistenerの境界をcursorで接続し、sequence順整列・event ID重複除去を行う購読helperを実装する
- [ ] 6.7 所有者不一致、active run、dispatch失敗、実行消失、購読切断、一時障害を公開errorへ変換する
- [ ] 6.8 fake backend・ChatStore・WorkspaceStoreで切断後再訪、重複dispatch、cancel競合、reconciliationを統合テストする

## 7. Terraformとリリース設定

- [ ] 7.1 `terraform/` のAgent Engine・Agent Gateway・Agent Registryリソースを除き、必要API、Artifact Registry、Cloud Run Job、GCSを定義する
- [ ] 7.2 Firestore databaseの作成または既存参照、location検証、session一覧とevent queryに必要なindexを定義する
- [ ] 7.3 snapshotと未commit objectの保持期間をGCS lifecycleへ反映し、Firestore run/event保持方針を設定へ追加する
- [ ] 7.4 制御サービスアカウントへ対象Jobのexecute/get/cancelとFirestore権限、jobサービスアカウントへ対象Firestore/GCSとAgent実行権限だけを付与する
- [ ] 7.5 schema version、Google Cloud、image、Job、Firestore、GCS、runtime、loggingを持つYAMLリリース設定モデルを実装する
- [ ] 7.6 未知field、秘密情報field、project・region・Firestore location不整合、危険なtask retry、無効なtimeoutをクラウド変更前に拒否する
- [ ] 7.7 秘密値を含まないサンプルリリース設定を追加し、正規化した設定からTerraform変数とJob更新入力を生成する
- [ ] 7.8 デプロイスクリプトで設定検証、実効値表示、Terraform plan/apply、Cloud Run Job更新を順序付きで実行し一時fileを削除する
- [ ] 7.9 指定Terraform binaryでfmt、init -backend=false、validateを行い、index、lifecycle、IAM scope、Job retry・timeoutを自動検査する
- [ ] 7.10 設定loader、Terraform runner、deployment adapterをfakeへ差し替え、新規・更新・不正設定・途中失敗を単体テストする

## 8. 実行可能サンプルとドキュメント

- [ ] 8.1 `example/agent.py` をASGI serverからrun IDを受け取るJobRunner entrypointへ変更する
- [ ] 8.2 `example/Dockerfile` と`.dockerignore`をCloud Run Job向けに変更し、固定依存、非root user、終了code、signal処理を検証する
- [ ] 8.3 Streamlitへ手入力identity providerと将来認証済みidentityへ差し替える境界を実装する
- [ ] 8.4 Streamlitにユーザー別session一覧、cursor pagination、新規作成、既存session再訪を実装する
- [ ] 8.5 Streamlitにrun予約・Job開始、active run表示、新規開始抑止、dispatch失敗表示を実装する
- [ ] 8.6 StreamlitにFirestore eventのリアルタイム購読、順序整列、重複除去、再接続cursor、最終結果表示を実装する
- [ ] 8.7 Streamlitに明示的cancel、cancel_requestedと停止完了の区別、reconciliation結果表示を実装する
- [ ] 8.8 sample agent、identity、session一覧、event購読、再訪、cancel UIを外部APIなしで検証するテストを追加する
- [ ] 8.9 READMEに新アーキテクチャ、識別子、3つのprovider port、Firestore data model、state machine、at-least-once保証を記載する
- [ ] 8.10 READMEにローカルテスト、Docker build/push、Terraform、Job配備、Streamlit、session再訪、リアルタイム応答、cancel手順を記載する
- [ ] 8.11 READMEに容量・保持期間・timeout・retry・DEBUG log・IAM・workspace非sandbox・障害reconciliationの運用境界を記載する
- [ ] 8.12 Agent Platform方式を廃止した理由、再利用対象、互換性のないAPI、旧sessionを自動移行しないことをmigration noteへ記載する

## 9. 旧経路の撤去と最終検証

- [ ] 9.1 Agent Platform runtime server、Sessions/Events adapter、Long-running Operation clientと関連公開exportを削除する
- [ ] 9.2 Agent Engine、Agent Gateway、Agent Registry、allowlist固有の設定・Terraform・デプロイ・テスト・依存を削除する
- [ ] 9.3 API名、model field、ログ、README、サンプルにAgent Platform固有前提が残っていないことを検索して確認する
- [ ] 9.4 in-memory fakeでrun予約からJobRunner、event購読、snapshot resume、cancel後の次runまでを通すend-to-end testを追加する
- [ ] 9.5 明示的な環境変数でのみ動くGoogle Cloud opt-in testでFirestore一覧・listener、Cloud Run Job、GCS resume、cancel、重複dispatchを検証する
- [ ] 9.6 全Python test、type、lint、Terraform検証、Docker build、OpenSpec strict validationを実行し失敗を解消する
