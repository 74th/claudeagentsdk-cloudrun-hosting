## Context

現リポジトリは Python 3.12 の最小プロジェクトであり、ホスティング実装はまだない。変更の動機と機能範囲は [proposal.md](./proposal.md)、観測可能な振る舞いは `specs/` を参照する。

参考実験では FastAPI の Agent Platform runtime contract、custom container の配備、Agent Platform Sessions/Events への追記を個別に確認している。一方、30分程度の長時間run、ブラウザ切断後の継続、同一Sessionの排他、Claude transcriptとworkspaceの同時復元は未検証である。

Agent Platform runtimeは複数インスタンスへスケールし得るため、実行中runをプロセスメモリだけで追跡できない。Claude Agent SDKのresumeにはClaudeセッションIDだけでなくtranscriptが必要であるため、Agent Platform Session Eventsを唯一の再開データにもできない。また、Agent Platform Sessions、Long-running Operation、GCSを横断するトランザクションは存在しない。

この制約から、実行状態、会話ミラー、再開データを次の3つへ分ける。

```text
Long-running Operation
  実行中・完了・失敗・キャンセルの正本

Agent Platform Session Events
  session/run対応、主要イベント、operation名、commit参照のミラー

GCS immutable snapshot
  Claude transcriptとworkspaceの再開用正本
```

## Goals / Non-Goals

**Goals:**

- 利用者がClaude Agent SDKエージェントを登録すると、通常・ストリーミング・長時間非同期実行へ適合できる小さな公開APIを定義する。
- ブラウザやStreamlitの接続終了後もrunを継続し、SessionとOperationから再訪できるようにする。
- 1Sessionのactive runを最大1件にし、キャンセル完了後にだけ次のrunを開始する。
- Claude transcriptとworkspaceを同じ不変snapshotとして保存し、別インスタンスで再開する。
- Session StoreミラーとClaude transcriptの乖離を後から比較できるようにする。
- Google API境界を差し替え可能にし、実サービスを使わない障害テストを可能にする。

**Non-Goals:**

- エージェントのプロンプト、ツール、権限ポリシー、業務ロジックを提供すること。
- Cloud SQL、Firestoreなどの独自Application DBや詳細イベントストアを構築すること。
- 切断したHTTPストリームそのものを途中位置から再開すること。
- workspaceディレクトリ分離をコンテナ、プロセス、OSレベルのセキュリティ境界にすること。
- workspaceのファイル単位差分同期、長期バックアップ、旧schemaや旧SDK transcriptの移行を提供すること。
- IAP、Secret Manager、OpenTelemetryを本フレームワークで構築すること。
- Agent Platform SessionsとGCS間の厳密な分散トランザクションを保証すること。

## Decisions

### 1. サーバー境界とクライアント境界を同じパッケージで提供する

`cas_hosting_adapter` は要求された3モジュールを主要境界とし、長時間run管理に必要な補助モジュールを追加する。

- `api_server.py`: Agent Platform runtime contract、入力検証、通常・ストリーミング・非同期実行のサーバー側入口、安全なエラー変換。
- `session_store.py`: Session作成・取得・一覧、所有者検証、主要イベント追記・列挙、run状態導出、transcript比較。
- `workspace_store.py`: GCS snapshotの保存・復元、run lock、一時領域、容量・安全性検査、孤立snapshot GC。
- `lifecycle.py`: prepare、run、commit、cleanup、cancel、timeoutを束ねるrunオーケストレーター。
- `agent_adapter.py`: Claude Agent SDKのrequest-scoped実行、transcript配置、SDKイベントの正規化。
- `client.py`: Streamlitなどのサーバー側フロントエンドがSession作成、非同期run開始、Operation確認、キャンセルを行う高水準クライアント。
- `models.py` / `errors.py` / `retry.py`: 識別子、イベント、設定、公開エラー、再試行規則。

サーバー側の主入口は `create_app(agent, settings, workspace_initializer=None)` とする。`agent` は並行安全なinstanceまたはrequestごとに生成するfactoryを受け付ける。長時間実行の利用者側入口は `HostingClient` とし、Agent PlatformのSDK/RESTの詳細をサンプルUIから隔離する。

非同期runではSession IDがOperation開始前に必要になるため、初回Sessionは `HostingClient` がSessions APIで先に作成する。通常・ストリーミングAPIでは従来どおりSession ID省略時にruntimeが作成できる。

代替案としてruntimeだけを公開し、StreamlitからAgent Platform SDKを直接操作する方法は、Session作成、run lock、operation紐付け、再試行がサンプルごとに重複するため採用しない。

### 2. session、run、operation、Claude session、workspaceを分離する

識別子の意味を次のように固定する。

| 識別子 | 意味 | 発行者 |
|---|---|---|
| `session_id` | 会話・分析テーマの単位。Agent Platform Sessionリソース名 | Agent Platform Sessions |
| `run_id` | Session内の1回の実行 | `HostingClient`。UUIDで事前発行 |
| `operation_name` | Agent Platform非同期実行の追跡 | Agent Platform async API |
| `claude_session_id` | Claude Agent SDK transcriptの会話ID | Claude Agent SDK |
| `workspace_id` | GCS/ローカルworkspace名前空間 | user IDとsession IDから決定的に生成 |

`run_id` はAgent Platform Session Eventのinvocation IDとしても使用する。すべてのログ、GCS input/output、snapshot、lockはsession IDとrun IDへ関連付ける。

公開 `session_id` にはAgent Platform Sessionリソース名を使い、独自対応表を持たない。取得時はproject/location/agent engineのprefixとSessionのuser IDを検証する。フレームワークは渡されたuser IDを信頼し、認証済みGoogleアカウントへの変換はフロントエンド/IAP境界の責務とする。

### 3. 長時間処理はAgent Platformの非同期実行を正本にする

通常APIとstreaming APIは短時間対話とローカル確認用に残す。30分程度の処理は `HostingClient.start_run()` からAgent Platformの非同期実行を呼び出し、Long-running Operationで追跡する。

```text
Streamlit / BFF                 Agent Platform              BYOC runtime
      │                               │                           │
      │ Session作成/選択              │                           │
      │ run_id発行                    │                           │
      │ GCS run lock取得              │                           │
      │ async実行開始 ───────────────▶│                           │
      │ operation_name ◀──────────────│                           │
      │ operation_bound event追記     │ ─────────────────────────▶│
      │                               │       Claude Agent SDK実行 │
      │ 切断してもよい                │                           │
      │                               │                           │
      │ 状態取得 ────────────────────▶│ Operation + Session Events │
```

非同期APIのinputにはuser ID、session ID、run ID、workspace ID、入力メッセージを含める。Agent PlatformがGCS URIによる入出力を要求する場合は、`runs/<run-id>/input.json` と `output.json` を `HostingClient` が管理する。使用SDK・リージョンにおける正確なasync/cancel request schemaは実装開始時の契約検証で固定し、公開モデルへ正規化する。

Operationは実行状態の正本、Session Eventsは再訪用の索引である。OperationがterminalなのにSession Eventsがrunningのままなら、状態取得時にOperationを優先してreconciliation eventを追記する。Operation成功でもsnapshot commitがなければ、runは再開可能なcompletedとして扱わずpersistence failureとする。

### 4. 1Sessionのactive runをGCS条件付きlockで制御する

Session Eventsの読取後に新規eventを追記するだけでは、複数インスタンスによる同時開始を原子的に防げない。そのため `locks/v1/<user-hash>/<session-hash>.json` を `if_generation_match=0` で作成し、active runの排他に使う。

lockには `run_id`、状態、作成時刻、期限、判明後の `operation_name` を含める。開始フローは次のとおりとする。

1. Session Eventsと既存lockからactive runを確認する。
2. run IDを発行し、短いpending期限を持つlockを条件付き作成する。
3. `run_requested` eventを追記して非同期Operationを開始する。
4. operation名をlockと `operation_bound` eventへ保存し、期限を最大実行時間と終了猶予から計算したrunning期限へ更新する。
5. Operationがterminalになったことを確認してから、lock generationを指定して削除する。

既存lockがあれば新runを拒否し、自動キャンセルしない。利用者が明示的にキャンセルし、Operation停止を確認した後にだけlockを解放する。クライアントが開始途中で停止したpending lockは短い期限後にreconcileし、Operationが存在しないことを確認して削除する。期限切れだけを理由に実行中Operationのlockを削除してはならない。

GCS lockはworkspaceのディレクトリ分離とは異なり、同時実行制御のための調整データである。将来Application DBを導入する場合はlockとrun registryをDBへ移せるようProtocol越しに扱う。

### 5. Session Eventsをrun registryと主要イベントのミラーにする

各eventを次のversion付きエンベロープとして保存する。

```json
{
  "schema_version": "1",
  "run_id": "...",
  "sequence": 42,
  "event_type": "progress",
  "timestamp": "...",
  "payload": {}
}
```

主要eventは少なくとも次を含む。

- `run_requested`
- `operation_bound`
- `run_started`
- `user_message`
- `agent_message`
- `tool_started` / `tool_completed` の要約
- `progress`
- `cancel_requested`
- `snapshot_committed`
- `completed` / `failed` / `cancelled` / `timed_out`

run内のsequenceは単調増加させ、復元時は `(run_id, sequence)` で物理的な重複を除く。アダプター独自の件数・payload上限は設けないが、Agent Platform API自体の上限やエラーはそのまま検出する。token deltaをすべて永続化することは必須にせず、会話と再訪に必要な主要イベントを保存する。

Session一覧は使用するAgent Platform Sessions SDKがuser IDによるlistを提供する場合だけ公開する。paginationを透過的に処理し、要求user IDに属するSessionだけを返す。listが利用できないSDKでは独自索引を新設せず `unsupported` を返す。

Sessionの最終committed時刻から既定1日を復元可能期間とする。期限超過Sessionは一覧上で区別できるが、通常の再開は `session_expired` とする。GCSの7日保持は障害調査用の猶予であり、復元可能期間を7日へ延長するものではない。

### 6. Claude transcriptをGCS snapshot内の再開用正本にする

Agent Platform Session EventsはClaude Agent SDKが必要とするローカルtranscriptを完全再現できるとは仮定しない。SDKのtranscript保存先をrequest固有の一時ルート配下へ固定し、workspaceと一緒にsnapshotへ含める。

snapshotの論理レイアウトは次のとおりとする。

```text
snapshot/
├── manifest.json
├── workspace/
└── claude-session/
    └── SDKが生成したtranscriptと再開用ファイル
```

Claude Agent SDKがセッション保存ディレクトリを直接指定できる場合はそのAPIを使う。直接指定できない場合は、子プロセスへrequest固有のHOMEまたは設定ディレクトリを渡し、プロセス全体の環境変数を変更しない。選択バージョンで安全に保存先を分離できなければ、サーバー起動時にconfiguration errorとする。

継続runでは最新 `snapshot_committed` eventが指すsnapshotを展開し、保存済みClaudeセッションIDでresumeする。SDKまたはschemaが非互換なら変換を試みず `session_incompatible` とする。

比較機能はSession EventsとClaude transcriptをそれぞれ正規化し、runごとにrole、text、tool名、tool入出力、順序を対応付ける。大きな値は比較用ハッシュも併記し、対応件数、片側だけのevent、順序差、内容差をレポートする。比較は読取専用で、どちらの保存内容も補正しない。

### 7. workspaceとtranscriptをrun単位の不変snapshotにする

GCSパスは生のuser/session値ではなくハッシュを用い、schemaとSDKバージョンを含める。

```text
locks/v1/<user-hash>/<session-hash>.json

cas/v1/sdk-<version>/users/<user-hash>/sessions/<session-hash>/
├── runs/<run-id>/input.json
├── runs/<run-id>/output.json
└── snapshots/<run-id>/snapshot.tar.gz
```

snapshotは `if_generation_match=0` で新規作成し、上書きしない。manifestとGCS metadataにはschema、SDK version、run ID、作成時刻、圧縮前後サイズ、SHA-256を含める。Sessionの `snapshot_committed` eventにはobject path、generation、SHA-256を記録し、復元時に一致を検証する。

この方式ではsnapshot upload後にSession event追記が失敗しても、そのsnapshotはcommit参照されないため次回復元へ混入しない。未commit snapshotは3時間後のGC候補とする。GCは候補objectがSessionの `snapshot_committed` eventから参照されていないことを再確認してから削除する。runtime終了時のopportunistic GCと管理用GCコマンドを提供し、定期実行方法を文書化する。最終的な上限としてGCS lifecycleで全対象objectを作成7日後に削除する。

圧縮前合計と圧縮後objectをそれぞれ100 MB以下とする。総容量以外のファイル数・単一ファイル上限は追加しない。tar作成・展開時は絶対path、`..`、device、FIFO、root外symlink/hardlinkを拒否する。ローカル一時ディレクトリはrunの全終了経路で即時削除する。

ファイル単位同期は転送量を減らせるが、削除検出と一貫したsnapshot作成が複雑になるため初期版では採用しない。

### 8. run lifecycleをprepare・execute・commit・finalizeに分ける

runtime内部の順序を次に固定する。

1. **prepare**
   - 1文字以上1,000文字以下の入力を検証し、Session所有者、run ID、GCS lockを確認する。
   - 最新committed snapshotを復元する。初回だけworkspace initializerを実行する。
   - `run_started` を追記し、最大実行時間とidle timeoutの監視を開始する。
2. **execute**
   - request-scoped Claude Agent SDKをworkspace/transcriptディレクトリで開始する。
   - SDK eventを正規化し、主要eventをSession Storeへsequence付きで追記する。
   - SDK eventまたは明示的なtool heartbeatでidle時刻を更新する。
3. **commit**
   - 正常終了時だけworkspaceとtranscriptを新しい不変snapshotへ保存する。
   - `snapshot_committed` eventを追記し、その後 `completed` とoutputを確定する。
4. **finalize**
   - completed、failed、cancelled、timed_outの状態を保存する。
   - ローカル一時ディレクトリを即時削除する。
   - Operation terminal確認後にrun lockを条件付き削除する。

エージェント失敗・cancel・timeout時は変更済みworkspace/transcriptをcommitせず、直前committed snapshotを維持する。障害調査はDEBUGログとSession Eventsで行う。

最大実行時間とidle timeoutは個別設定とし、既定はいずれも1,800秒とする。idleはSDK eventまたはadapterが認識したtool heartbeatを受信しない時間で計測する。最大実行時間またはidle timeout到達時はClaude Agent SDKへ協調的停止を要求し、runを `timed_out` とする。

SIGTERM時は新規run受付を停止し、進行中SDKへcancelを伝え、可能な範囲でterminal eventを追記して一時領域を削除する。プラットフォームの終了猶予内に完了できなければ成功扱いにせず、Operation側のerrorまたは再調整へ委ねる。Agent Platformからruntimeへのcancel/SIGTERM伝播と猶予時間は実環境PoCで確認する。

### 9. 状態導出とキャンセルを明示的なstate machineにする

```text
requested ─▶ running ───────────────▶ completed
                 │
                 ├─▶ failed
                 ├─▶ timed_out
                 └─▶ cancel_requested ─▶ cancelled
```

`running` と `cancel_requested` をactiveとみなす。cancel要求時点ではlockを維持し、Operation APIがterminal cancellationを返すまで `cancelled` にしない。新run開始要求はactive runのID、operation名、状態を返して拒否する。自動的に既存runをcancelしてはならない。

Session EventsとOperationが食い違う場合は、次の優先順位で状態を導出する。

1. 実行中・cancel・失敗の事実はOperationを優先する。
2. 再開可能な成功は `snapshot_committed` とOperation成功の両方を要求する。
3. Session EventsにだけterminalがありOperationが実行中ならactiveとして扱う。
4. reconciliation結果を新しいSession Eventとして残す。

### 10. 再試行を外部APIの安全な操作に限定する

Session Store、GCS、Operation APIのread、および決定的ID・precondition・idempotency keyで保護されたwriteだけを自動再試行する。対象はnetwork error、408、429、5xxで、既定最大3回、full jitter付き指数backoffを使用する。初期待機0.5秒、上限5秒とする。

次は自動再試行しない。

- Claude Agent SDKのrun全体
- 認証・認可、validation、not-found
- GCS precondition conflict
- 非互換transcript/schema
- cancel済みrun

async開始APIがidempotency keyを提供する場合はrun IDを使用する。提供しない場合、Operation作成成功と `operation_bound` event追記の間には孤立Operationの可能性が残るため、lockとrun IDを用いた照合・管理者向け診断で検出する。

### 11. エラーとログを正規化する

内部例外は `validation`、`session`、`workspace`、`agent`、`operation`、`conflict`、`timeout`、`configuration` の段階、安定した公開code、retryable属性へ変換する。API応答へstack trace、credential、内部pathを含めない。ログはuser ID、session ID、run ID、operation名を構造化fieldとして持つ。

既定ログレベルはINFOとし、状態遷移と識別子を記録する。DEBUGでは要求どおりprompt、tool input、tool result、SDK eventを加工せず記録する。マスキング処理は行わないため、DEBUGは開発環境専用であり、ログ保管側のアクセス制御が必要である。OpenTelemetry連携は今回実装しない。

### 12. Streamlitは簡易BFFとして動作する

ブラウザからAgent Platformを直接呼ばず、Streamlitサーバー側で `HostingClient` を使う。サンプルではアカウント名を手入力し、identity provider関数が返すuser IDとして扱う。将来は同じ関数をIAP headerからGoogleアカウントを取得する実装へ差し替える。

StreamlitはSession一覧が利用できれば過去Sessionを表示し、選択SessionのEventsから最新runとoperation名を復元する。active run中は新規開始を無効化し、状態・主要イベント・cancel操作を表示する。ブラウザを閉じてもOperationは継続し、再訪時は切断したstreamへつなぎ直さず、Session Eventsの最後のsequence以降とOperation状態を取得する。

独自Application DBを持たないため、operation名の永続索引は `operation_bound` Session Eventである。Session一覧APIが利用できない場合、利用者は既知のSession IDを入力して再訪する。

### 13. YAMLをリリースの正本とし、配備スクリプトが全体をオーケストレーションする

利用者は `scripts/deploy_agent.py --config releases/prod.yaml` をリリースの単一エントリーポイントとして使用する。YAMLは `schema_version` を持ち、release、Google Cloud、Agent、storage、runtime、egressの設定を一元管理する。リポジトリには秘密値を含まない `releases/example.yaml` を置き、環境別設定はこれを複製して管理できるようにする。

概念上の設定は次の形とする。

```yaml
schema_version: "1"
release:
  name: production
google_cloud:
  project_id: example-project
  region: us-central1
agent:
  display_name: claude-agent
  container_image: us-central1-docker.pkg.dev/example-project/agents/claude-agent:v1
  service_account: agent-runtime@example-project.iam.gserviceaccount.com
storage:
  bucket: example-agent-state
  retention_days: 7
runtime:
  max_execution_seconds: 1800
  idle_timeout_seconds: 1800
  session_restore_days: 1
  workspace_max_bytes: 100000000
  log_level: INFO
egress:
  mode: allowlist
  gateway:
    name: claude-agent-egress
    enforcement_mode: ENFORCE
    registry_location: global
  allowed_hosts:
    - api.github.com
```

設定モデルは未知のkeyを拒否し、秘密値を受け取るfieldを持たない。スクリプトは、YAML読込、schema検証、既定値適用と正規化、実効許可リスト算出、変更対象の表示、Terraform plan/apply、Agent Engine作成・更新の順に処理する。検証や必須ホスト導出に失敗した場合はTerraformやAgent Platformを変更しない。

`terraform/` は必要API、Artifact Registry、uniform bucket-level accessを設定したGCSバケット、7日lifecycle、runtime service account、Agent Platformと対象bucket限定IAMを作成する。不変snapshotと作成preconditionを使うためobject versioningは有効化せず、削除済みデータがnoncurrent versionとして保持期限を超えて残らないようにする。Secret Managerは作成せず、秘密値をoutputしない。配備スクリプトは正規化済み設定から一時的なTerraform変数ファイルを生成するため、利用者がYAMLとtfvarsへ同じ値を二重入力する必要はない。一時ファイルは秘密値を含まないが、処理終了時に削除する。

Agent Engine custom containerの作成・更新も `scripts/deploy_agent.py` が担当する。通常・streaming・async/cancelに必要な操作、container image、runtime service account、GCS URI、実行・idle timeout、復元期間、容量、ログレベルを設定する。SDKのprivate methodに依存する箇所は1モジュールへ閉じ込め、選択バージョンのcontract testを置く。

外向き通信は次の2方式とする。

- `unrestricted`: 既定値。Gateway関連Terraform resourceを作らず、Agent EngineにもGatewayを関連付けない。実行環境で通常許可される外部ホストへ接続できる。
- `allowlist`: Agentと同じproject・regionへAgent-to-Anywhere Gatewayを構築し、Agent Engineへ関連付ける。Agent Registryのlocationは対応するlocationを設定し、初期既定を `global` とする。利用者指定ホストとフレームワーク必須ホストを外部endpointとして登録し、runtime service accountには登録済み宛先だけを利用するIAMを付与する。

許可ホストは完全一致のDNS hostnameへ正規化し、URL、path、port、IP address、wildcardを拒否する。Agent Registry resource IDはrelease名とhostnameから決定的に生成し、同じ設定の再適用で重複しないようにする。`ENFORCE` は未登録または未認可の宛先を遮断し、`DRY_RUN` は違反候補を記録するが遮断しない。`allowlist` の本番導入では、まず `DRY_RUN` で観測してから `ENFORCE` へ切り替える。

Agent GatewayはAgentの全外向き通信へ影響し得るため、利用者指定ホストだけでなく、構成済み機能が使用するGCS、Agent Platform、Vertex AI、Claude on Vertex等の完全一致ホストをversion管理したresolverで導出する。resolverは利用者指定と自動補完を区別した実効リストを返す。利用するAPI・region・endpoint形式に対するhost mappingを安全に決定できなければ、通信断を起こす不完全なallowlistを適用せずconfiguration errorとする。

### 14. 実サービスPoCを実装の検証ゲートにする

単体テストではSessions/Events、GCS、Operation、Claude Agent SDK境界をProtocol越しのfakeへ差し替える。実サービスを使うopt-in PoCでは、実装本体を広げる前に次を確認する。

- 選択リージョンとSDKにおけるasync開始、状態取得、cancelのrequest/response。
- BYOC runtimeへ渡るinput/output形式と最大実行時間。
- cancelがClaude Agent SDK processへ伝わる方法、SIGTERMと終了猶予。
- Session一覧のfilter、pagination、user IDの返却形式。
- Session Eventsのevent schema、順序、重複時の振る舞い。
- Claude Agent SDK transcript保存先のrequest単位分離と、別instanceでのresume。
- 同じrunについてSessionミラーとClaude transcriptがどこまで一致し、何が片側だけに存在するか。

PoC結果がSDKのmethod名やpayload mappingだけを変える場合は境界adapterを更新する。Session一覧が利用できなければ仕様どおりunsupportedとする。Sessionミラーがtranscriptと完全一致しても、初期版ではGCS transcriptを再開用正本として維持し、比較結果を将来の簡素化判断に使う。

## Risks / Trade-offs

- [Agent Platformのasync/cancelやSession APIがSDK更新で変わる] → 依存versionを固定し、SDK固有処理をclient adapterへ閉じ込め、opt-in contract testを置く。
- [Operation作成後、operation名をSessionへ保存する前にclientが停止する] → run ID付きGCS lockと決定的input URIを残し、idempotency keyが利用可能なら使用し、孤立Operationを診断対象にする。
- [Session EventsとGCS間で部分失敗する] → 不変snapshotを先に保存し、commit eventが参照したsnapshotだけを復元する。未参照snapshotは3時間後のGC候補にする。
- [GCS lockが残留する] → lockだけで削除せずOperation状態を照合し、terminalまたはOperation未作成を確認して条件付き削除する。
- [Session EventsとClaude transcriptが乖離する] → transcriptを再開用正本にし、比較レポートで欠落・追加・順序差を可視化する。
- [Claude Agent SDKのtranscript場所をrequestごとに分離できない] → 起動時configuration errorとし、process-global環境を書き換えた多重実行を行わない。
- [同一Sessionの同時開始] → GCSの新規作成preconditionでactive lockを原子的に取得し、Operation停止確認まで維持する。
- [100 MB snapshotの圧縮・転送が実行時間を圧迫する] → 圧縮前後の上限を早期検査し、commit時間を計測する。初期版では差分同期しない。
- [tar展開によるpath traversal] → entry種別と解決先を検証し、専用一時directory外への書込みを拒否する。
- [DEBUGログに機密データが含まれる] → DEBUGを開発環境専用として明示し、既定INFOではprompt/tool payloadを出力しない。
- [workspace分離をtenant sandboxと誤認する] → セッション別directoryは整理・復元単位であり、悪意あるtenant隔離ではないことを文書化する。
- [GCS 7日削除前でもSessionが1日で期限切れになる] → 1日は通常復元契約、7日は障害調査と最終削除猶予として目的を分ける。
- [SIGTERM中に状態保存できない] → 未完了runを成功扱いせず、Operationと残留lockから次回reconcileする。
- [YAMLと実際のクラウド変更が一致しない] → YAMLを唯一の利用者入力とし、正規化後の同じ設定からTerraform変数とAgent Engine設定を生成して変更前に表示する。
- [Gatewayがフレームワーク必須通信まで遮断する] → 必須host resolverでGoogle Cloudとmodel endpointを補完し、導出不能時はfail closedとする。初回は `DRY_RUN` で観測する。
- [hostnameの別名やregional endpointが許可リストから漏れる] → wildcardを使わず、実際に利用する完全一致hostnameをversion管理してcontract testと実サービスPoCで検証する。
- [GatewayまたはAgent RegistryのAPI・対応locationが変更される] → providerとAPI versionを固定し、GatewayはAgentと同じproject・region、Registryは対応locationという制約を事前検証する。

## Migration Plan

1. Agent Platform async/cancel、Session一覧、Claude transcript配置を実サービスPoCで確認し、依存versionとcontract fixtureを固定する。
2. 公開モデル、Protocol、fakeを作成し、Session Eventsのrun registryとGCS lock/snapshotを障害注入テストで固める。
3. 通常・streaming・asyncのruntimeと `HostingClient` を実装し、切断、cancel、timeout、再接続、別instance resumeを検証する。
4. `releases/example.yaml` と設定validationを実装し、同じ正規化済み設定からTerraform変数とAgent Engine設定が生成されることを確認する。
5. `unrestricted` でTerraformを検証用projectへ適用し、GCS lifecycle、最小権限IAM、Gateway resourceが作られないことを確認する。
6. `allowlist` の `DRY_RUN` でAgent GatewayとAgent Registryを適用し、サンプルAgentの実通信から必須host一覧を検証する。
7. 検証済み許可リストを `ENFORCE` へ切り替え、登録済みhostへの成功と指定外hostへの拒否を確認する。
8. サンプルcontainerを新規Agent Engineへ配備し、Streamlitから30分runの開始、ブラウザ切断、再訪、cancel、Session一覧を確認する。
9. 同じrunのSession StoreミラーとClaude transcriptを比較し、乖離レポートと既知の制限を文書化する。

既存の本番resourceや保存形式はないためdata migrationは不要である。rollbackはAgent Engineを以前のimageへ戻すか新規Engineを削除する。GCSはTerraform destroyだけで即時消去せず、lifecycleまたは明示的な管理操作で削除する。

## Open Questions

- 選択するAgent Platform SDK/リージョンでのasync開始、cancel、BYOC入出力の正確なAPI名とpayloadは何か。
- Agent Platform Sessionsのlistがuser ID filterと必要なpaginationを提供するか。
- 固定するClaude Agent SDK versionで、transcript保存先をrequest単位に分離する最も安定した方法は何か。
- Agent Platform cancelおよびinstance終了時のSIGTERMがClaude Agent SDK subprocessへどう伝播し、何秒の猶予が得られるか。

これらは境界adapterと設定値を確定するPoC項目であり、GCS transcriptを正本とすること、1Session 1 active run、不変snapshot、Operationを実行状態の正本とする全体方針は変更しない。
