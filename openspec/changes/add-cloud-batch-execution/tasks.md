## 1. 設定モデルと実行基盤境界

- [ ] 1.1 リリース設定を新 schema version へ更新し、`execution_platform` と Cloud Run／Batch 別設定を型付けして、混在項目・予約値 `gke`・未対応 version をクラウド変更前に拒否する
- [ ] 1.2 schema version 2 から `execution_platform: cloud-run` を明示する移行エラー／手順と、Terraform 変数への変換を追加する
- [ ] 1.3 Google Cloud composition root を共通 Firestore／Storage client と基盤別 execution client／backend の生成に分離し、選択した backend だけを ControlClient へ注入する
- [ ] 1.4 実行参照を異なる backend へ渡した場合の検証を Cloud Run と共通契約テストへ追加する

## 2. Google Cloud Batchバックエンド

- [ ] 2.1 Google Cloud Batch SDK 依存と、SDK 型を公開境界へ漏らさない Batch client adapter の骨格を追加する
- [ ] 2.2 run ID から命名制約に適合する決定的 Job ID を生成し、1 task・parallelism 1・retry 0・run ID と共通非秘密環境設定を持つ Batch Job の開始処理を実装する
- [ ] 2.3 `ALREADY_EXISTS` と保存済み実行参照を既存 Job へ収束させ、重複開始でエージェント処理を増やさない冪等ディスパッチを実装する
- [ ] 2.4 Batch Job 状態を `pending`／`running`／`succeeded`／`failed`／`cancelled` へ正規化し、not found・permission・quota・validation・一時障害を共通ドメインエラーへ変換する
- [ ] 2.5 active Job の削除によるキャンセル、終端 Job の冪等キャンセル、Firestore の cancel request と Job 消失を用いた終端確定を実装する
- [ ] 2.6 fake Batch client で Job 定義、開始冪等性、全状態写像、照会エラー、キャンセル、未知状態の安全側処理を単体テストする

## 3. Terraformによる実行基盤切替

- [ ] 3.1 `execution_platform` と Batch の Job ID prefix、machine type、CPU、memory、timeout 設定を Terraform variables に追加し、`cloud-run`／`cloud-batch` 以外を事前拒否する
- [ ] 3.2 Cloud Run Job と Cloud Run 専用 IAM を `cloud-run` 選択時だけ作成し、既存 resource address の変更に必要な `moved` block または state migration 手順を追加する
- [ ] 3.3 Batch API と制御主体の最小 Batch 権限、およびジョブ実行サービスアカウント単体への利用権限を `cloud-batch` 選択時だけ構成する
- [ ] 3.4 Firestore、index／TTL、GCS lifecycle、Artifact Registry、ジョブ実行主体のデータ／Vertex AI 権限を両基盤の共通リソースとして維持する
- [ ] 3.5 Terraform の静的検証を拡張し、両選択肢で非選択リソース／IAM がなく、Batch の task count・parallelism・retry が安全な固定値であることをテストする
- [ ] 3.6 Cloud Run から Batch への切替 plan で Firestore database と GCS bucket が置換／削除されないことを検証する手順を追加する

## 4. Exampleと利用手順

- [ ] 4.1 既存 Cloud Run リリース example を新 schema と明示的な `execution_platform: cloud-run` へ移行する
- [ ] 4.2 image、region、Batch Job ID prefix、machine type、CPU／memory、timeout を含み秘密情報を含まない Cloud Batch リリース設定 example を追加する
- [ ] 4.3 README に Batch 設定の検証、Terraform plan／apply、run 起動、状態確認、ログ確認、キャンセル、Cloud Run へのロールバック手順を追加する
- [ ] 4.4 GKE は予約済みだが未実装であり、今回の example／Terraform 適用対象外であることを文書化する

## 5. 統合検証

- [ ] 5.1 リリース設定、factory、ControlClient／reconciler のテストを両 backend 選択に対して実行し、Firestore／GCS の既存テストが変更なく通ることを確認する
- [ ] 5.2 Cloud Run と Cloud Batch の Terraform init／validate と example ごとの plan を実行し、API、IAM、条件付きリソースを確認する
- [ ] 5.3 テスト用 Google Cloud 環境で Batch run の開始、Firestore 入力取得、GCS workspace 利用、イベント永続化、成功、失敗、キャンセル、重複開始を確認する
- [ ] 5.4 全自動テスト、型検査、lint を実行し、Cloud Run の既存実行動作に回帰がないことを確認する
