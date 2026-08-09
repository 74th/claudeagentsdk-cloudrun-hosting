## 1. 共通保持設定

- [x] 1.1 リリース設定の schema version を更新し、既定 30 日の `retention_days` を追加して旧3保持フィールドに移行エラーを返す
- [x] 1.2 `release.example.yaml` とテスト用リリース設定を `retention_days: 30` へ更新する
- [x] 1.3 Terraform の保持変数を共通 `retention_days` へ統合し、Cloud Run Job の `RUN_RETENTION_DAYS` と GCS lifecycle へ同じ値を渡す
- [x] 1.4 Firestore codec、chat store、factory が共通設定値を使用し、未指定時は既定30日となることを確認する

## 2. GCS lifecycleの統一

- [x] 2.1 `cas/v1/` prefix へ1日を適用する lifecycle rule を削除し、全 GCS object を共通保持期間で削除対象にする
- [x] 2.2 workspace、transcript、一時 object のどのパスにも共通 rule 以外の短期削除条件が適用されないことを Terraform 検証へ追加する

## 3. 検証と配備表示

- [x] 3.1 リリース設定の既定値、任意の有効日数、旧フィールド拒否、Terraform 変数への共通値伝播を単体テストする
- [x] 3.2 Firestore のセッション・run・イベントが既定30日および設定日数の `expires_at` を保存し、期限切れを非表示にするテストを更新する
- [x] 3.3 Terraform 検証で Firestore 3 collection group の TTL、GCS の単一共通 rule、Job の保持環境変数を確認する
- [x] 3.4 配備前表示に Firestore と GCS の共通保持日数、全対象、非同期削除である旨を追加する

## 4. ドキュメントと実環境確認

- [x] 4.1 README に旧3フィールドから `retention_days` への移行、30日の既定、削除遅延と復旧制約を記載する
- [x] 4.2 Terraform plan と全自動テストを実行し、Firestore と全 GCS object の実効保持期間が同じ設定値であることを確認する
- [x] 4.3 適用後に Cloud Run Job の保持環境変数、Firestore TTL policy、GCS lifecycle がリリース設定の値と一致することを確認する
