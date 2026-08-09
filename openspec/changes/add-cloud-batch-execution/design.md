## Context

現在のドメイン境界には `ExecutionBackend` とプロバイダー名を持つ `ExecutionReference` があり、JobRunner、Firestore、GCS は既に実行基盤から分離されている。一方、Google Cloud の composition root、リリース設定、Terraform は Cloud Run のクライアントと静的 Job リソースへ固定されている。Cloud Batch は Cloud Run と異なり、事前作成した Job 定義を実行するのではなく、run ごとに完全な Job 定義を作成し、キャンセルには Job 削除を用いる。

## Goals / Non-Goals

**Goals:**

- `ExecutionBackend` の契約を変えずに Cloud Run と Batch を構成時に選択する。
- 同一のコンテナ entrypoint、Firestore run、GCS workspace、owner claim を両基盤で再利用する。
- Terraform plan で選択した基盤だけの API、IAM、実行リソースが明確になる構成にする。
- Cloud Run から Batch へ切り替えても永続データを保持する。

**Non-Goals:**

- GKE cluster、Pod／Job manifest、Kubernetes クライアントの実装。
- 複数実行基盤への同時ディスパッチ、run ごとの動的な基盤選択、既に active な run の基盤間移送。
- Firestore／GCS の schema、JobRunner の処理、コンテナイメージの基盤別分岐。

## Decisions

### 1. 既存の実行ポートに Batch adapter を追加する

`ExecutionBackend.start/get/cancel` と `ExecutionReference(backend, name, identity)` を共通境界として維持し、Batch SDK 型は新しい adapter 内に閉じ込める。`backend` は Cloud Run と Batch を識別し、異なる adapter に属する参照を受け取った場合は検証エラーにする。

代替案として Cloud Run／Batch のメソッドを ControlClient に直接追加する方法は、制御フローと SDK 状態を結合し、将来の GKE adapter 追加でも分岐を増やすため採用しない。ポート自体を新設する案も、現行契約が必要な開始・照会・キャンセルを既に表現できるため採用しない。

### 2. Batch Job IDをrun IDから決定的に生成する

Batch adapter は UUID の run ID から Batch の命名制約に適合する Job ID を生成し、`create_job` の `ALREADY_EXISTS` を既存 Job の取得へ正規化する。ControlClient が保存済み参照を返す既存の重複防止に加え、参照保存直前の再試行でも同じ Job に収束させる。Job 定義は 1 task、parallelism 1、max retry count 0 とし、`RUN_ID` だけを run 固有値として渡す。

ランダム Job ID は作成成功後かつ参照保存前の障害で重複を防げないため採用しない。長寿命の Batch Job テンプレート相当を Terraform で管理する案は Batch API の run ごとの Job 作成モデルと合わないため、Terraform は設定、API、IAM、共通基盤を管理し、実際の Job は adapter が作成する。

### 3. Batch状態とエラーを共通語彙へ明示変換する

Batch の作成待ち／キュー／スケジュール状態は `pending`、実行状態は `running`、成功は `succeeded`、失敗は `failed` へ写像する。ユーザーキャンセル時は削除要求を送り、永続側の cancel request と Job 消失を組み合わせて `cancelled` と判定する。通常の照会で Job が消失した場合は not-found エラーとし、成功とは解釈しない。Google API の not found、permission、quota、invalid argument、一時障害は Cloud Run adapter と同じドメインエラー分類へ変換する。

削除済み Job の状態を Batch API だけから復元することはできないため、キャンセル意図と終端状態の正本は従来どおり Firestore に置く。Batch Job の自動削除／保持期間管理は、監査可能性を損なわない運用値を別途明示し、run 状態確定前の削除を行わない。

### 4. release schemaを更新しcomposition rootで一度だけ選択する

リリース設定を新 schema version へ更新し、`execution_platform` に `cloud-run`、`cloud-batch`、予約値 `gke` を定義する。`cloud-run` は Job 名、`cloud-batch` は Batch Job ID prefix、machine type、CPU／memory など基盤別設定を検証する。`gke` は語彙として認識するが、明確な「未対応」エラーでクラウド変更前に拒否する。schema version 2 からは `execution_platform: cloud-run` を追加する手順を示す。

設定から生成した composition root は選択した adapter と必要な SDK client だけを構築する。Firestore と Storage client は共通とし、フロントエンドや ControlClient に基盤分岐を持ち込まない。

既存 schema version 2 で暗黙に Cloud Run を選ぶ方法は移行が容易だが、設定の意味が schema 間で曖昧になるため採用しない。移行時に Cloud Run を明示させ、未知／混在フィールドを早期拒否する。

### 5. Terraformは条件付きリソースと条件付きIAMを用いる

Terraform 変数 `execution_platform` は実装済みの `cloud-run`／`cloud-batch` だけを適用可能とし、Cloud Run Job と Cloud Run 制御 IAM は前者でのみ作る。Batch API、有効化後の Batch 制御 IAM、ジョブサービスアカウント利用権限は後者でのみ作る。Firestore、GCS、Artifact Registry、ジョブ実行サービスアカウントとそのデータ／Vertex AI 権限は共通リソースとして維持する。

単一モジュール内の `count`／`for_each` による条件分岐を採用し、当面は同じ state で排他的な切替を表現する。基盤ごとに Terraform root を複製する案は共通データ基盤の drift と切替時の state 移行を増やすため採用しない。GKE は実装時に専用 module と必要な network／cluster 入力を追加する。

## Risks / Trade-offs

- [Batch Job の削除後は API から終端理由を照会できない] → Firestore の cancel request／終端状態を正本にし、reconciler が確定するまで自動削除しない。
- [Cloud Run と Batch で状態遷移やエラー理由が完全には一致しない] → 共通の有限状態とドメインエラーに明示写像し、SDK の未知状態は成功ではなく安全側の一時エラーとして扱う。
- [条件付き Terraform resource の導入で既存 address が変わる可能性がある] → `moved` block または state migration 手順を用意し、plan で Firestore／GCS の置換がないことを切替前に確認する。
- [Batch の machine type／CPU／memory の組み合わせが region 在庫に依存する] → 設定レベルの構文検証と API エラー分類を行い、example は一般的な構成を採用する。
- [制御主体へサービスアカウント利用権限が必要になる] → 対象をジョブ実行サービスアカウント単体へ限定し、プロジェクト全体の Service Account User を付与しない。

## Migration Plan

1. 新 release schema と Cloud Run 明示設定を先に導入し、既存 Cloud Run plan が実質的に同一であることを確認する。
2. Batch adapter と状態／エラー変換を fake client による契約テストで追加する。
3. Terraform の条件分岐、Batch API／IAM、Batch example を追加し、両基盤の validate／plan を取得する。
4. テスト環境で Batch 設定を plan し、Firestore と GCS が置換されないことを確認して適用する。
5. Batch で run の開始、イベント永続化、完了、失敗、キャンセル、重複開始を確認してから制御側設定を切り替える。

ロールバックは `execution_platform: cloud-run` に戻して再適用し、Cloud Run adapter を選択する。Firestore と GCS は共通 resource address を維持し、切替やロールバックで削除しない。切替時点で active な Batch run は移送せず、完了または明示キャンセル後に切り替える。
