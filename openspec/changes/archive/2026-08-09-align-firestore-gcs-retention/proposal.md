## Why

Firestore のチャットデータと GCS の workspace・transcript snapshot は同じセッション履歴を構成するが、現在の配備では Firestore は 30 日、`cas/v1/` 配下の GCS object は広すぎる短期 lifecycle により実効 1 日で削除対象となる。全保存データを同じ既定 30 日へ揃え、リリース設定から一貫して変更できるようにする必要がある。

## What Changes

- Firestore のセッション、run、イベントと、GCS の workspace・transcript・一時 object に共通する保持期間を導入し、既定を 30 日とする。
- `release.example.yaml` を含むリリース設定で共通保持期間を明示的に設定可能にする。
- 共通保持期間を Terraform、Cloud Run Job、Firestore の `expires_at`、GCS lifecycle へ同じ値で渡す。
- `cas/v1/` 全体を 1 日で削除対象にする短期 lifecycle を廃止し、GCS object 全体へ共通保持期間を適用する。
- plan 前の表示とテストで、Firestore TTL と GCS lifecycle の対象、保持日数、削除の非同期性を確認可能にする。
- **BREAKING**: `run_retention_days`、`snapshot_retention_days`、`uncommitted_retention_days` を単一の `retention_days` へ置き換える。

## Capabilities

### New Capabilities

なし。

### Modified Capabilities

- `cloud-run-job-deployment`: Firestore とすべての GCS 保存データへ、リリース設定由来の共通保持期間を適用する。
- `firestore-chat-store`: セッション、run、イベントの既定 30 日を維持しつつ、共通リリース設定で保持期間を変更可能にする。
- `workspace-object-store`: commit 状態にかかわらず、すべての GCS 保存データへ共通保持期間を適用する。

## Impact

リリース設定スキーマ、`release.example.yaml`、Terraform 変数と GCS lifecycle、Cloud Run Job の環境変数、Firestore codec/store、配備前表示、保持期間に関するテストおよび運用ドキュメントが影響を受ける。旧保持期間フィールドを使用する設定ファイルには `retention_days` への移行が必要になる。
