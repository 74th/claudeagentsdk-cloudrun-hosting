# Google Cloud SDK / API 契約の固定

## 選定 version と method

| 用途 | 固定 version | 使用する公開 method |
| --- | --- | --- |
| Cloud Run Jobs | `google-cloud-run==0.16.1` | `JobsClient.run_job`、`ExecutionsClient.get_execution`、`ExecutionsClient.cancel_execution` |
| Firestore | `google-cloud-firestore==2.28.0` | transaction、document get/set/update、query、`on_snapshot` |
| GCS | `google-cloud-storage==2.19.0` | `Blob.upload_from_*` の `if_generation_match=0`、`reload`、generation 指定 download/delete |
| 認証 | `google-auth==2.47.0` | Application Default Credentials / 実行 identity |

Cloud Run SDK の `get_execution` と `cancel_execution` は `JobsClient` ではなく
`ExecutionsClient` にある。コア port にはこれらの SDK 型を公開しない。

## 正規化と retry

| provider の観測 | 正規化結果 | retry |
| --- | --- | --- |
| Execution 未開始、`Completed=Unknown` | `pending` | status read の一時障害だけ |
| `Started=True` かつ未終端 | `running` | status read の一時障害だけ |
| `Completed=True` | `succeeded` | なし |
| `Completed=False, reason=Cancelled` | `cancelled` | cancel は終端状態なら冪等 |
| `Completed=False` のその他 | `failed` | なし |
| transport の `UNAVAILABLE`、`DEADLINE_EXCEEDED`、HTTP 408/429/5xx | 公開 temporary error | idempotent get/cancel、event ID 付き append、条件付き snapshot 作成だけ |
| `NOT_FOUND`、`PERMISSION_DENIED`、`RESOURCE_EXHAUSTED`、`FAILED_PRECONDITION`、入力/region 不一致 | 安定した公開 error | なし |

## 初期構成上限

- Cloud Run Job `task retry` は `0`。Cloud Run Execution は at-least-once であり、
  retry を有効化しても JobRunner の Firestore claim なしには Claude 実行を再試行しない。
- Job task timeout と run 最大時間の既定は 1,800 秒、idle timeout は 1,800 秒、
  event payload / batch は `firestore-event-limits.md` の値を使う。
- `RUN_ID` は UUID とし、実行 override に渡せるのは run ID と非秘密設定だけである。
- Firestore は `us-central1` Native Standard、GCS は `US-CENTRAL1` bucket を使う。

## opt-in 再実行手順

1. `GOOGLE_CLOUD_PROJECT=nnyn-dev`、`GOOGLE_CLOUD_REGION=us-central1` を明示する。
2. `test-claudesdk-cloudrun` に新しい UUID の `RUN_ID` override で Execution を開始する。
3. Cloud Run / Firestore / GCS の各 live fixture に記載した probe を実行する。
4. 実行後は probe object と Firestore probe document を削除し、Cloud Run Execution は
   cancel または終端を確認する。

