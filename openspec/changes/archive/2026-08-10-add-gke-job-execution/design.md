## Context

制御側は `ExecutionBackend` を介して Cloud Run Jobs または Cloud Batch を操作し、ジョブ側は共通の `JobRunner` と ADC で Firestore、GCS、Vertex AI を利用している。リリース設定は `execution_platform` と Terraform enable flag を分離している一方、`gke` は予約値として拒否されている。

対象は project `nnyn-dev`、region `asia-northeast1` の既存 GKE Autopilot cluster `autopilot` であり、frontend と Terraform の実行環境には `gcloud container clusters get-credentials` で生成した kubeconfig が存在する。GKE の workload は Workload Identity Federation for GKE を利用できる。

## Goals / Non-Goals

**Goals:**

- Kubernetes API の差異を共通実行状態と既存エラー分類の内側へ閉じ込める。
- 同じコンテナイメージと JobRunner を Cloud Run、Cloud Batch、GKE で共有する。
- GKE Job の GCP 権限を KSA principal に直接付与し、長寿命鍵と workload 専用 GSA を不要にする。
- GKE を無効化した構成では既存の Cloud Run／Batch の plan と実行を変えない。

**Non-Goals:**

- GKE cluster 自体、node pool、Workload Identity pool を新規作成または管理しない。
- 複数 cluster への自動振り分け、Job キュー、CronJob、常駐 worker は導入しない。
- Cloud Run／Batch の既存 GSA を KSA principal 方式へ移行しない。
- アプリケーション内で kubeconfig や GCP credential を発行・保存しない。

## Decisions

### GKEバックエンドはKubernetes Batch APIを直接利用する

`GKEJobsBackend` は frontend の既存 kubeconfig から Kubernetes client を構築し、namespace 内の Job を作成、取得、削除する。実行参照は backend 名、`namespace/job-name`、run ID を保持する。Job 名は run UUID から決定的に生成し、run ID label も付ける。作成時の HTTP 409 は同名 Job の label が同じ run ID なら既存実行として返し、異なる場合は衝突エラーにする。

代替として Kubernetes manifest を `kubectl` subprocess で操作する案は、エラー分類とテストが文字列出力に依存するため採用しない。Google Cloud の GKE API は cluster 管理 API であり Job API ではないため、実行操作には使わない。

### Job仕様は単一実行と既存ランタイム契約を維持する

Job は `parallelism=1`、`completions=1`、`backoffLimit=0`、`restartPolicy=Never` とし、`activeDeadlineSeconds` にリリース設定の timeout を使う。Pod は構成済み KSA を指定し、`RUN_ID` と既存の project、Firestore database、bucket、Vertex region/model、保持期間、質問 timeout、log level だけを環境変数で受け取る。入力本文や credential は渡さない。

Autopilot に適合するよう CPU／memory の requests と limits を GKE 設定として明示する。Job 完了後の調査時間を確保しつつ残留を制限するため `ttlSecondsAfterFinished` も設定可能にする。

状態は Job conditions と active/succeeded/failed カウンタから `pending`、`running`、`succeeded`、`failed` へ変換する。API の 404、401/403、409、429、5xx／接続障害は既存の not-found、permission、validation/conflict、quota、temporary 系エラーへ明示的に分類する。

### キャンセルは制御側の状態記録とforeground deletionを組み合わせる

既存 lifecycle がキャンセル要求を永続化した後、GKE バックエンドは foreground propagation で Job と Pod を削除し、削除完了を短時間 polling して `cancelled` を返す。同じ run の再キャンセルは永続化済みの終端状態を制御側が返す。キャンセル記録のない実行参照が 404 になった場合は、予期しない実行消失として扱う。

Pod を単独削除する案は Job controller が再作成する可能性があるため採用しない。Job の suspend は terminal cancellation を表現せず、再開可能状態になるため採用しない。

### Terraformは既存clusterを対象にnamespace、KSA、IAMだけを管理する

`enable_gke`、cluster 名／region、namespace、KSA 名、kubeconfig context を Terraform 変数へ追加する。Kubernetes provider は検証環境に既にある kubeconfig/context を利用し、`enable_gke=true` のときだけ `claude-agent` namespace と KSA を作成する。cluster 本体は data source または入力値として参照するだけにする。

Project Number は Google project data source から取得し、次の principal URI を組み立てる。

```text
principal://iam.googleapis.com/projects/${project_number}/locations/global/workloadIdentityPools/${project_id}.svc.id.goog/subject/ns/${namespace}/sa/${ksa_name}
```

GCS は bucket IAM member を使用する。Firestore/Datastore と Vertex AI のように必要な操作を対象リソース単位で表現できない権限だけ project IAM member を使用する。KSA へ GSA annotation は付けず、GSA、JSON key、`roles/iam.workloadIdentityUser` binding は作らない。

Terraform が namespace/KSA を管理せずアプリ起動時に都度生成する案は、IAM principal と Kubernetes identity の不整合を plan で検出できないため採用しない。

### release設定は基盤選択と構築フラグを引き続き分離する

schema version を更新し、`enable_gke` と platform 固有の `gke` block（cluster、cluster region、namespace、KSA、kube context、CPU、memory、Job TTL）を追加する。`execution_platform: gke` は `enable_gke=true` を要求するが、別基盤の enable flag は変更しない。GKE cluster region は既存の `region == firestore_location` 制約とは別項目にし、今回の `asia-northeast1` cluster から既存 `us-central1` Firestore へ接続できるようにする。

`release.gke.yaml` は検証環境の cluster と namespace を指定する配備例兼実行設定とし、Terraform の tfvars は作成する基盤の組み合わせを制御する。kubeconfig は秘密情報を含み得るため release YAML や Terraform state へ内容を埋め込まず、path/context の参照だけを扱う。

## Risks / Trade-offs

- [frontend の kubeconfig が失効または context 違いになる] → 起動前の設定検証と Kubernetes API の permission/temporary エラー分類を追加し、対象 context を明示する。
- [Job 作成成功後に応答が失われ重複 dispatch になる] → run ID 由来の決定的な Job 名と label、409 時の同一性確認で既存 Job を回収する。
- [Job 削除後は Kubernetes API だけからキャンセルと予期しない消失を区別できない] → 削除前に制御ストアへキャンセル要求を記録し、その永続状態を判定元にする。
- [Autopilot が resource request を補正または拒否する] → 明示的な requests/limits を設定し、実 cluster の dry-run と実 Job で検証する。
- [KSA principal に対応しない API 権限が判明する] → まず直接 principal で live test し、例外が確認された権限だけ理由を記録して別案を検討する。初期実装では GSA impersonation へ自動 fallback しない。
- [GKE region とデータ region の分離で通信遅延・転送料が増える] → location を独立設定として明示し、今回の既存環境では許容した上でテスト結果を記録する。

## Migration Plan

1. Kubernetes client 依存、GKE backend、設定モデル、factory と単体テストを追加する。
2. Terraform に conditional な Kubernetes provider/resource、KSA principal IAM、変数検証を追加し、Cloud Run／Batch の既存 plan が維持されることを確認する。
3. `release.gke.yaml` と GKE 用 tfvars を追加し、既存 kubeconfig の `autopilot` context を対象として検証する。
4. 更新したコンテナイメージを Artifact Registry へ push し、digest を release/tfvars に反映する。
5. Terraform plan を確認して apply し、`claude-agent` namespace、KSA、IAM binding を検査する。
6. frontend から GKE Job を起動し、Pod の ADC による Firestore、GCS、Vertex AI アクセス、状態遷移、完了とキャンセルを確認する。

rollback は `execution_platform` を既存の `cloud-run` または `cloud-batch` に戻して frontend を再起動する。`enable_gke=false` の Terraform apply で GKE 専用 namespace/KSA/IAM を除去する前に active Job がないことを確認する。既存の Cloud Run／Batch リソースと保存データは変更しない。
