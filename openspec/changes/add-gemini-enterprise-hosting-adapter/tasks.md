## 1. 外部API契約の検証ゲート

- [ ] 1.1 参考実験の依存versionを起点にClaude Agent SDK、Google Cloud SDK、Agent Platform SDKの候補versionを固定したPoC環境を作る
- [ ] 1.2 選択リージョンで通常・streaming・async実行、GCS入出力、Long-running Operationの開始・状態取得・結果取得の実request/responseを記録する
- [ ] 1.3 async runへのcancel要求、Operationの状態遷移、BYOC runtimeおよびClaude Agent SDK subprocessへの停止伝播、SIGTERM猶予を実測する
- [ ] 1.4 Agent Platform Sessionsのcreate/get/list、user ID filter、pagination、Events append/list、重複eventの振る舞いを実サービスで確認する
- [ ] 1.5 Claude Agent SDKのtranscript保存先をrequest単位で分離し、保存したtranscriptを別process/instanceへ移してresumeできることを検証する
- [ ] 1.6 同一runのClaude transcriptとAgent Platform Session Eventsを取得し、対応する会話・tool・結果と片側だけの情報を比較した基準データを保存する
- [ ] 1.7 PoC結果から依存version、SDK固有method、async/cancel payload、transcript配置方法を確定し、再実行可能なcontract fixtureと検証手順を残す
- [ ] 1.8 Agent-to-Anywhere Gateway、Agent Registry外部endpoint、関連IAM、Agent EngineへのGateway関連付けを実サービスで確認し、対応project・region・Registry location、`DRY_RUN`・`ENFORCE`、拒否ログのcontract fixtureを残す

## 2. パッケージ基盤と公開モデル

- [ ] 2.1 `pyproject.toml` に固定したruntime・開発依存、パッケージ設定、test・type・lint設定を追加し、`uv lock`でlockfileを生成する
- [ ] 2.2 `cas_hosting_adapter` のmodule構造と公開exportを作成し、server、client、session、workspace、agent、lifecycleの境界を定義する
- [ ] 2.3 session ID、run ID、operation名、ClaudeセッションID、workspace ID、run状態、version付きevent、snapshot manifestのモデルを定義する
- [ ] 2.4 最大message 1,000文字、最大実行時間1,800秒、idle timeout 1,800秒、復元期間1日、snapshot圧縮前後100 MB、ログレベルを持つ設定モデルを定義する
- [ ] 2.5 agent instance/factory、Session/Events、GCS snapshot/lock、Operation、clockのProtocolとin-memory fakeを定義する
- [ ] 2.6 validation・session・workspace・agent・operation・conflict・timeout・configurationの公開error codeとretryable属性を定義する
- [ ] 2.7 network error・408・429・5xxだけを最大3回、初期0.5秒・上限5秒のfull-jitter指数backoffで再試行するutilityを実装する
- [ ] 2.8 user/session/run/operationをfieldに持つ構造化loggingを実装し、INFOとraw prompt/tool payloadを含むDEBUGの出力境界をテストする

## 3. Agent Platform Session Storeとrun registry

- [ ] 3.1 固定SDKを薄く包むSessions/Events client adapterを実装し、create/get時のproject・location・agent engine prefixとuser所有者を検証する
- [ ] 3.2 Session ID省略時の作成、指定時の取得、not-found、所有者不一致、最終commitから1日を既定とするsession-expired判定を実装する
- [ ] 3.3 SDKが対応する場合のuser別Session listとpaginationを実装し、非対応SDKでは推測せずunsupportedを返す
- [ ] 3.4 schema version、run ID、sequence、event type、timestamp、payloadを持つSession Eventのserialize・append・listを実装する
- [ ] 3.5 `(run_id, sequence)`で重複を除き、主要eventからrunとoperationの対応およびrunning・cancel_requested・cancelled・completed・failed・timed_outを導出する
- [ ] 3.6 `snapshot_committed` eventのobject path・generation・SHA-256を検証して最新の再開可能snapshot参照を取得する
- [ ] 3.7 Operation状態を優先して古いSession状態を補正し、Operation成功でもsnapshot commitがないrunをpersistence failureとするreconciliationを実装する
- [ ] 3.8 SessionミラーとClaude transcriptを正規化し、対応件数・欠落・追加・順序差・内容差を出力する読取専用比較機能を実装する
- [ ] 3.9 Session作成・一覧・期限・所有者・event順序・重複・run状態導出・commit参照・比較・一時障害を網羅する単体テストを追加する

## 4. GCS workspace・transcript・run lock

- [ ] 4.1 user/sessionをhash化し、schema・SDK version・run IDを含むGCS input/output、snapshot、active lockのpath生成を実装する
- [ ] 4.2 requestごとの一時root配下に`workspace/`と`claude-session/`を分離して作成し、全終了経路で即時削除するcontext managerを実装する
- [ ] 4.3 絶対path、親参照、device、FIFO、root外symlink/hardlinkを拒否するtar作成・展開処理を実装する
- [ ] 4.4 Claude sessionを含む圧縮前合計と圧縮後objectを各100 MB以下に検証し、ファイル数・単一ファイルには追加上限を設けない処理を実装する
- [ ] 4.5 schema・SDK version・run ID・作成時刻・容量・SHA-256を持つmanifestを生成し、`if_generation_match=0`でrun固有の不変snapshotを保存する
- [ ] 4.6 committed eventが指すobject path・generation・SHA-256を照合してsnapshotを復元し、未commit・破損・非互換snapshotを拒否する
- [ ] 4.7 committed snapshotがない場合だけworkspace initializerを実行し、初期化失敗時にagent実行とsnapshot保存を止める
- [ ] 4.8 `if_generation_match=0`でpending active-run lockを取得し、operation binding後の更新とgeneration指定削除を行うlock storeを実装する
- [ ] 4.9 既存lockのOperation状態を照合し、active runを拒否し、terminalまたはOperation未作成を確認した期限切れlockだけをreconcileする
- [ ] 4.10 作成から3時間を過ぎたsnapshotについてSession commit参照を再確認して削除するopportunistic GCと管理CLIを実装する
- [ ] 4.11 path分離、安全なarchive、容量、snapshot完全性、同名作成競合、lock競合・残留、GC、一時directory cleanupの単体テストを追加する

## 5. Claude Agent SDK adapterとrun lifecycle

- [ ] 5.1 固定SDKの保存先指定機能または子process専用環境を使い、process-global環境を変更せずtranscriptを`claude-session/`へ出力するadapterを実装する
- [ ] 5.2 agent instanceまたはrequest-scoped factoryを受け付け、初回queryと保存済みClaudeセッションIDによるresumeを統一的に実行する
- [ ] 5.3 Claude SDK messageをuser・agent・tool started/completed・progress・terminal eventへ正規化し、run内sequenceとDEBUG raw logを付与する
- [ ] 5.4 prepare段階として入力、Session所有者、run ID、active lockを検証し、最新committed snapshotの復元または初期化を行う
- [ ] 5.5 execute段階としてSDK eventを処理し、主要eventをSessionへ追記し、SDK eventまたはtool heartbeatでidle時刻を更新する
- [ ] 5.6 最大実行時間とidle timeoutを個別に監視し、既定1,800秒超過時にSDKへ協調停止を要求してtimed_outを記録する
- [ ] 5.7 commit段階として正常終了時だけworkspaceとtranscriptの不変snapshotを保存し、`snapshot_committed`後にcompletedとoutputを確定する
- [ ] 5.8 failed・cancelled・timed_out時は変更snapshotをcommitせず、terminal eventを保存して一時directoryを削除する
- [ ] 5.9 SIGTERM時に新規run受付を止め、SDKへcancelを伝え、可能な範囲で状態保存とcleanupを行い、未完了runを成功扱いしない処理を実装する
- [ ] 5.10 初回・resume・別instance・event順序・timeout・cancel・SIGTERM・snapshot/Session部分失敗を網羅するlifecycleテストを追加する

## 6. Agent Platform serverとHostingClient

- [ ] 6.1 `api_server.py` に `create_app`、health check、固定したAgent Platform runtime contractの通常・streaming・async側entrypointを実装する
- [ ] 6.2 通常APIでuser ID、任意Session ID、messageを受け、Session ID、run ID、commit済み最終応答を返す
- [ ] 6.3 streaming APIでSession/run metadata、応答event、commit後のcompleteまたは構造化errorを返し、切断streamの再開は行わない
- [ ] 6.4 async runtime inputからuser/session/run/workspaceを検証し、Long-running Operation配下で接続に依存せずlifecycleを完了する
- [ ] 6.5 `HostingClient` にSessionの作成・取得・一覧と、run ID発行、input GCS保存、active lock取得、async Operation開始を実装する
- [ ] 6.6 Operation開始後にoperation名をlockへ保存して`operation_bound` eventを追記し、開始途中の失敗と孤立Operationを診断可能にする
- [ ] 6.7 Operation状態、Session Events、snapshot commitを照合してrun状態・主要event・最終result/errorを返すstatus APIを実装する
- [ ] 6.8 cancel要求をOperationへ送り、cancel_requestedを記録し、terminal cancellation確認後だけcancelledへ更新してlockを解放する
- [ ] 6.9 1,000文字入力、active run、not-found、所有者、timeout、非互換、競合、一時障害を安定したHTTP/client errorへ変換する
- [ ] 6.10 serverとHostingClientの通常・streaming・async開始・切断継続・状態再取得・cancel・排他・error契約テストを追加する

## 7. Terraformとデプロイ・運用スクリプト

- [ ] 7.1 `terraform/` にprovider/version、必要API、Artifact Registry、uniform bucket-level access付きGCSバケットを定義し、object versioningを有効化しない
- [ ] 7.2 GCS objectを既定7日後に削除するlifecycle ruleを保持日数変数付きで定義する
- [ ] 7.3 runtime service account、Agent Platform Sessions・Operations利用権限、対象bucket限定object権限を定義し、Secret Managerを作成しない
- [ ] 7.4 `schema_version`、release、Google Cloud、Agent、storage、runtime、egressを持ち、未知のkeyと秘密情報用fieldを拒否するYAMLリリース設定モデルを実装する
- [ ] 7.5 `releases/example.yaml` を追加し、project、region、container image、名称、service account、GCS、実行制限、`unrestricted`・`allowlist`の記述例を秘密値なしで示す
- [ ] 7.6 `scripts/deploy_agent.py --config <path>` にYAML読込、schema version検証、既定値適用、正規化、field単位errorを実装し、検証失敗時は外部変更を開始しない
- [ ] 7.7 構成済み機能とproject・regionからGCS、Agent Platform、Vertex AI、Claude on Vertexの必須完全一致hostnameを導出し、利用者指定hostと由来付きで統合するresolverを実装する
- [ ] 7.8 URL、path、port、IP address、wildcardを拒否してDNS hostnameを正規化し、同じ実効host集合から決定的なAgent Registry resource IDを生成する
- [ ] 7.9 `unrestricted` ではGateway関連resourceを0件にし、`allowlist` ではAgent-to-Anywhere Gateway、Agent Registry外部endpoint、宛先限定IAMを条件付き作成するTerraform構成を追加する
- [ ] 7.10 AgentとGatewayのproject・region一致、Agent Registryの対応location、Gateway名、非空の利用者許可host、`DRY_RUN`・`ENFORCE`を検証する
- [ ] 7.11 正規化済みYAMLから秘密値を含まない一時Terraform変数ファイルを生成し、plan/apply後または失敗時に削除するオーケストレーションを実装する
- [ ] 7.12 Terraform変更前にproject、region、Agent名、image、egress mode、Gateway mode、利用者指定・自動補完を区別した実効許可hostを表示する
- [ ] 7.13 `scripts/deploy_agent.py` に通常・streaming・async/cancel操作、image、service account、GCS、timeout、復元期間、容量、ログレベル、任意のGateway関連付けのAgent Engine作成・更新を実装する
- [ ] 7.14 指定Terraform binaryで`fmt`、`init -backend=false`、`validate`を実行し、versioning無効・7日lifecycle・IAM scope・広域role不在・egress mode別resource数を自動テストする
- [ ] 7.15 設定loader、必須host resolver、Terraform runner、SDK固有deployment adapterをtest doubleへ差し替え、schema不正、組合せ不整合、`unrestricted`、`DRY_RUN`、`ENFORCE`、新規・更新・operation待機・途中失敗・一時file削除を単体テストする

## 8. 実行可能なサンプル

- [ ] 8.1 `example/agent.py` に最小Claude Agent SDK agent、workspace initializer、環境変数設定、ASGI app生成を実装する
- [ ] 8.2 `example/Dockerfile` と `.dockerignore` を作成し、固定依存、非root user、所定port、health checkでsample APIを起動する
- [ ] 8.3 Streamlitへ手入力アカウント名を返すidentity provider境界を実装し、将来IAP header実装へ差し替え可能にする
- [ ] 8.4 StreamlitにSession作成・一覧または既知ID入力、async run開始、active run表示、主要event、状態polling、result表示を実装する
- [ ] 8.5 Streamlitに明示的cancel、cancel_requested/停止完了表示、停止完了までの新規run無効化、ブラウザ再訪時のrun復元を実装する
- [ ] 8.6 sample agent登録、Docker設定、identity差替え、Session再訪、async状態・cancel UIを外部APIなしで検証するテストを追加する

## 9. ドキュメントと統合検証

- [ ] 9.1 READMEにアーキテクチャ、5種類の識別子、Operation・Sessionミラー・GCS正本の責務、公開server/client APIを記載する
- [ ] 9.2 READMEにローカル起動、Docker build/push、YAMLリリース設定、`--config`によるTerraformとAgent Engineのdeploy、Streamlit、async run開始・再訪・cancelの手順を記載する
- [ ] 9.3 READMEに1日復元、3時間孤立GC、7日GCS削除、100 MB、timeout、retry、DEBUG raw log、workspace非sandbox、Secret Manager対象外、および`unrestricted`・`allowlist`の通信境界を記載する
- [ ] 9.4 Session StoreミラーとClaude transcriptの比較CLI/API、比較結果の読み方、SDK非互換時のerrorを文書化する
- [ ] 9.5 fake Session/GCS/Operationとfake agentを使い、async開始から切断、別client再訪、snapshot resume、cancel後の次runまでを通す統合テストを追加する
- [ ] 9.6 明示的な環境変数でのみ動くGoogle Cloud opt-in testを追加し、async Operation、Session list、別instance resume、cancel/SIGTERM、lock、GC、ミラー比較、Gateway `DRY_RUN`観測、`ENFORCE`時の登録host成功と未登録host拒否を検証する
- [ ] 9.7 全Python test、type・lint、Terraform検証、Docker build、OpenSpec strict validationを実行し、失敗を解消する
