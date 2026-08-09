## MODIFIED Requirements

### Requirement: 保存データの保持期限を構成する
デプロイ構成は Firestore のセッション、run、イベントと、GCS の workspace・transcript・一時 object に共通する保持期間をリリース設定で変更可能にし、既定値と対象を配備前に表示しなければならない（SHALL）。共通保持期間の既定値は基準時刻またはオブジェクト作成時刻から 30 日とし、名前付き Firestore database のセッション、run、イベント各 collection group には有効期限フィールドによる自動削除を、GCS bucket には同じ日数の lifecycle による自動削除を構成しなければならない（SHALL）。Firestore と GCS に異なる通常保持期間を設定できてはならない（MUST NOT）。

#### Scenario: 既定保持期間で計画する
- **WHEN** 利用者がリリース設定で保持期間を省略する
- **THEN** システムは Firestore のセッション・run・イベントと GCS の全保存 object に共通する 30 日の保持期間を plan 前に表示する

#### Scenario: Firestore TTLを計画する
- **WHEN** 有効な名前付き Firestore database と GCS bucket を指定して Terraform plan を作成する
- **THEN** plan はその database のセッション、run、イベント各 collection group に同じ有効期限フィールドの TTL ポリシーを含み、bucket の全保存 object に同じ保持日数の削除 lifecycle を含む

#### Scenario: リリース設定で保持期間を変更する
- **WHEN** 利用者がリリース設定の共通保持期間へ有効な日数を指定する
- **THEN** Terraform、Cloud Run Job、Firestore TTL 対象ドキュメントの有効期限、および GCS lifecycle に同じ日数が渡される

#### Scenario: 親セッションが先に削除される
- **WHEN** TTL によりセッション親ドキュメントが run またはイベントより先に物理削除される
- **THEN** run とイベントも各自の TTL ポリシーにより自動削除の対象であり、親削除だけに依存して無期限に残留しない
