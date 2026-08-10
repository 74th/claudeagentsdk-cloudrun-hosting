## Why

現在の実行バックエンドは Cloud Run Jobs と Google Cloud Batch に限られており、既存の GKE Autopilot クラスタをエージェント実行基盤として利用できない。frontend が保持する Kubernetes 認証情報から 1 run ごとの Kubernetes Job を操作し、既存の Firestore・GCS・JobRunner を再利用できる選択肢を追加する。

## What Changes

- 共通の実行バックエンド契約を実装する GKE Job バックエンドを追加し、run の開始、状態照会、キャンセル、重複ディスパッチ防止を提供する。
- `claude-agent` namespace、Kubernetes ServiceAccount、Job の実行設定を追加する。
- GKE 上の KSA を Workload Identity Federation for GKE の IAM principal として直接利用し、専用 GCP Service Account、JSON 鍵、KSA の GSA annotation を作成しない。
- Terraform の tfvars で Cloud Run、Cloud Batch、GKE の基盤構築を有効・無効化できるようにする。
- `release.gke.yaml` とリリース設定の `execution_platform: gke` により、frontend が利用する実行バックエンドを GKE Job に切り替えられるようにする。
- `autopilot` クラスタ（project `nnyn-dev`、region `asia-northeast1`）へ接続済みの検証環境で、イメージ更新、Terraform 適用、実 Job の起動を検証対象にする。

## Capabilities

### New Capabilities

なし。

### Modified Capabilities

- `job-execution-backend`: GKE Job を共通契約で開始・照会・キャンセルし、状態とエラーを正規化する要件を追加する。
- `cloud-run-job-deployment`: GKE 実行基盤、Kubernetes リソース、KSA principal への最小権限 IAM、tfvars と release YAML による切り替えの要件を追加する。

## Impact

- 実行バックエンドの実装、factory、Google Cloud/Kubernetes クライアント構築、設定モデルと依存パッケージが影響を受ける。
- Terraform の provider、変数、IAM、Kubernetes namespace／ServiceAccount と、デプロイ・検証スクリプトが影響を受ける。
- `release.gke.yaml`、サンプル設定、テスト、README の運用手順を追加・更新する。
- frontend 実行環境には対象 GKE クラスタを操作できる kubeconfig が必要になる。
