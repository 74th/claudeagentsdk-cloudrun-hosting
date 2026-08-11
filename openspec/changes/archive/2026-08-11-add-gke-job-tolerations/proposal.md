## Why

現在の GKE Job バックエンドは Pod の tolerations を設定できないため、taint で分離された特定用途の NodePool へエージェント Job をスケジュールできない。リリース設定から Kubernetes の toleration を安全に指定し、生成される Job の Pod テンプレートへ反映できるようにする。

## What Changes

- GKE リリース設定に複数の toleration を追加し、各要素で `key`、`operator`、`value`、`effect` を指定可能にする。
- 設定した tolerations を GKE Job の `spec.template.spec.tolerations` へ欠落や変換なく反映する。
- toleration の Kubernetes 制約をリリース設定の検証時に確認し、不正な組み合わせは Job 作成前に拒否する。
- 単体テストで設定の伝播と Job manifest を検証し、Autopilot 検証環境では `Equal` 以外の operator を指定して作成済み Pod の YAML まで反映を確認する。
- 検証後、リポジトリに保存する GKE リリース例では tolerations 設定をコメントアウトし、通常の配備で有効化しない状態に戻す。

## Capabilities

### New Capabilities

なし。

### Modified Capabilities

- `job-execution-backend`: GKE Job の Pod にリリース設定由来の tolerations を適用し、taint された NodePool へスケジュール可能にする要件を追加する。

## Impact

- GKE リリース設定モデル、アプリケーション設定への変換、GKE backend の Job manifest 組み立てが影響を受ける。
- `release.gke.yaml`（要件で言及された GKE リリース例に相当）、設定・factory・GKE backend のテストが影響を受ける。
- Kubernetes Job API の既存フィールドだけを利用し、新しい外部依存や公開 API の破壊的変更は追加しない。
