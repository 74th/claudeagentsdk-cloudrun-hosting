## MODIFIED Requirements

### Requirement: 保存データの保持期限を構成する
デプロイ構成は snapshot と一時オブジェクトの lifecycle、および Firestore のセッション、run、イベントの保持方針を設定可能にし、既定値を配備前に表示しなければならない（SHALL）。Firestore の既定保持期間は基準時刻から 30 日とし、名前付き database のセッション、run、イベント各 collection group に対して有効期限フィールドによる自動削除を構成しなければならない（SHALL）。

#### Scenario: 既定保持期間で計画する
- **WHEN** 利用者が保持期間を省略する
- **THEN** システムは 30 日の Firestore 保持期間、対象となるセッション・run・イベント、および snapshot と一時オブジェクトに採用した保持値を plan 前に表示する

#### Scenario: Firestore TTLを計画する
- **WHEN** 有効な名前付き Firestore database を指定して Terraform plan を作成する
- **THEN** plan はその database のセッション、run、イベント各 collection group に同じ有効期限フィールドの TTL ポリシーを含む

#### Scenario: 親セッションが先に削除される
- **WHEN** TTL によりセッション親ドキュメントが run またはイベントより先に物理削除される
- **THEN** run とイベントも各自の TTL ポリシーにより自動削除の対象であり、親削除だけに依存して無期限に残留しない
