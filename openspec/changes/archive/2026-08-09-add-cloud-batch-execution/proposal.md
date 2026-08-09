## Why

現在の実行バックエンドと配備構成は Cloud Run Jobs を前提としており、同じ Firestore・GCS ベースの制御／永続化モデルを保ったまま Google Cloud Batch や将来の GKE Pod へ実行先を切り替えられない。まず Cloud Batch を選択可能にし、実行基盤固有処理を共通インターフェイスの背後へ分離する。

## What Changes

- 実行バックエンドの生成と設定をプロバイダー非依存の境界へ分離し、Cloud Run Jobs と Google Cloud Batch を同じ開始・状態取得・キャンセル契約で扱う。
- 1 run を 1 Batch Job として起動し、run ID のみをコンテナへ渡して Firestore と GCS の既存データモデルを継続利用する Cloud Batch バックエンドを追加する。
- リリース設定に実行 backend の選択項目を追加し、Terraform には `enable_cloud_run`／`enable_cloud_batch` を追加する。検証環境では両方を有効化して、YAML の差し替えだけで backend を切り替えられるようにする。
- Cloud Batch で実行するためのリリース設定／Terraform 変数の example と運用手順を追加する。
- 将来値として GKE Pod を設定モデル上で識別できる設計余地を残すが、この変更では GKE リソースやバックエンドを実装せず、選択された場合は配備前に未対応として拒否する。

## Capabilities

### New Capabilities

なし。

### Modified Capabilities

- `job-execution-backend`: 共通実行契約の Cloud Batch 実装、実行参照／状態の正規化、開始・照会・キャンセルの振る舞いを追加する。
- `cloud-run-job-deployment`: Cloud Run 固定の配備契約を実行基盤選択型へ拡張し、Cloud Batch のリソース、IAM、設定例と未対応 GKE 値の事前拒否を追加する。

## Impact

- `cas_hosting_adapter` の実行バックエンド、共通モデル、Google Cloud クライアント生成、設定ファクトリー
- リリース設定の schema・検証・デプロイスクリプトと既存 Cloud Run 設定の互換性
- Terraform の API 有効化、enable flag によるリソース、サービスアカウント、IAM、outputs／variables
- Google Cloud Batch SDK 依存、単体テスト、Terraform 検証、Cloud Batch example、README
- Firestore と GCS の保存形式および JobRunner の run 所有権契約は変更しない
