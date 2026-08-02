# 既存実装の移行監査

監査対象は、archive 済み change `add-gemini-enterprise-hosting-adapter` の完了済み
タスクと、その成果である現行の追跡済みソース・テストである。旧 change の未完了
タスクは実装根拠にせず、本 change の対応タスクで改めて検証する。

| 区分 | 現行成果 | 移行方針 | 本 change の対応タスク |
| --- | --- | --- | --- |
| provider 非依存で再利用 | `errors.py` の公開例外、`retry.py` の安全な再試行、`logging.py` の構造化ログ | 新しい user/session/run/execution の識別子と公開 error code に合わせて維持・適応する | 2.8, 4.5, 6.7 |
| provider 非依存で再利用 | `agent_adapter.py` の Claude SDK 呼出し、request 固有環境、resume の入口 | `AgentRunner` port と正規化イベントへ移し、JobRunner 内だけで利用する | 5.5, 5.6, 5.8 |
| provider 非依存で再利用 | `workspace_store.py` の hash 化、request 一時ディレクトリ、安全な archive 展開、容量・hash 検査、immutable snapshot | GCS 型を公開せず `WorkspaceStore` と snapshot reference に移す。active lock は移行しない | 2.6, 5.1--5.4, 5.11--5.12 |
| provider 非依存で再利用 | `protocols.py` の in-memory object store とテスト double の方式 | `ExecutionBackend`、`ChatStore`、`WorkspaceStore` ごとの fake と contract test に分割する | 2.4--2.7, 3.11, 4.7, 6.8 |
| 契約変更して改修 | `models.py` の run、event、snapshot、設定モデル | Agent Platform resource name / operation を廃し、user、opaque session ID、run、execution reference、cursor、workspace を分離する | 2.2--2.3, 7.5--7.7 |
| 契約変更して改修 | `client.py` の run 開始・status・cancel・再調整 | GCS lock と LRO の代わりに Firestore の reserve/claim と `ExecutionBackend` を使う control client に置換する | 3.4--3.9, 4.2--4.6, 6.1--6.6 |
| 契約変更して改修 | `lifecycle.py` の prepare/execute/commit | Firestore claim・heartbeat・cancel、workspace snapshot、Job の signal/timeout を扱う `JobRunner` に置換する | 5.7--5.12 |
| 契約変更して改修 | `google_adapters.py` の Google SDK 隔離 | Cloud Run Jobs、Firestore、GCS の個別 adapter に分解し、Google SDK 型を core API から排除する | 2.9, 3.1--3.10, 4.1--4.7, 5.2 |
| Agent Platform 固有で廃止 | `api_server.py`、`main.py` の ASGI runtime / reasoning-engine endpoint | Cloud Run Job の run ID entrypoint と Streamlit control client へ置換後に削除する | 8.1, 9.1 |
| Agent Platform 固有で廃止 | `session_store.py` の `SessionsApi`、`EventsApi`、`GoogleSessionStore`、mirror/transcript 比較 | Firestore `ChatStore` と event cursor 購読へ置換後に削除する。旧 mirror 比較は移行しない | 3.1--3.11, 9.1 |
| Agent Platform 固有で廃止 | `protocols.py` の `Operations`、`ActiveRunLockStore`、`InMemoryOperations` と `workspace_store.py` の `RunLockStore` | `ExecutionBackend` と Firestore transaction claim へ置換後に削除する | 2.4--2.5, 3.4--3.5, 4.1--4.7, 9.1 |
| Agent Platform 固有で廃止 | `google_adapters.py` の `AgentPlatformOperations` と `google-cloud-aiplatform` 依存 | Cloud Run Jobs adapter と固定依存へ置換後に削除する | 2.1, 4.1--4.7, 9.2 |
| Agent Platform 固有で廃止 | 旧 Agent Engine / Gateway / Registry / allowlist の PoC・設計・デプロイ前提 | Cloud Run Job、Firestore、GCS、最小権限 IAM へ置換し、旧方式の実装・テスト・文書から除去する | 7.1--7.10, 9.2--9.3 |

## 完了済み旧タスクからの扱い

- 再利用候補は、旧タスク 2.6--2.8、4.1--4.8、5.7 の実装を対象に、対応する
  新タスクの unit / contract test で意味論を再確認してから採用する。
- 旧タスク 3.1、3.7--3.8、6.5--6.8 は Agent Platform API に結合しているため、
  コードをそのまま引き継がない。必要な利用者向け動作だけを Firestore と
  Cloud Run Jobs の契約として再実装する。
- archive 内の未完了タスクおよび PoC は、Cloud Run Jobs / Firestore / GCS の
  新しい opt-in 検証（本 change 1.2--1.7、9.5）で置き換える。

