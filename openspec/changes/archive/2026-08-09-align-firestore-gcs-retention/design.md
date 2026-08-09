## Context

現在のリリース設定には `run_retention_days`、`snapshot_retention_days`、`uncommitted_retention_days` があり、Firestore と GCS の保持期間を別々に指定できる。Firestore と GCS 全体の既定は 30 日だが、GCS の `cas/v1/` prefix には 1 日 lifecycle も設定され、実際の workspace・transcript snapshot を含むため早い方のルールで削除対象となる。

TTL と GCS lifecycle はどちらも期限到達後の非同期削除であり、厳密な削除時刻は保証しない。Firestore は既に期限切れドキュメントを読み取り時に除外する。

## Goals / Non-Goals

**Goals:**

- Firestore とすべての GCS 保存データの保持日数に単一の設定元を持たせる。
- 新規配備の既定を 30 日とし、`release.example.yaml` と各環境のリリース設定で変更可能にする。
- `cas/v1/` へ適用される 1 日 lifecycle を除去し、全 GCS object の実効保持期間を揃える。

**Non-Goals:**

- 期限到達時刻ちょうどの物理削除を保証しない。
- Firestore export や GCS object versioning を長期バックアップとして自動構築しない。
- セッション単位、commit 状態、object prefix ごとの個別保持期間は導入しない。

## Decisions

### `retention_days`を単一の設定元にする

リリース設定へ既定 30 日の `retention_days` を定義し、Terraform の同名変数、Cloud Run Job の `RUN_RETENTION_DAYS`、GCS bucket lifecycle へ同じ値を渡す。既存3フィールドを残して一致検証する案は設定の重複と将来の不整合を残すため採用しない。リリース設定の schema version を更新し、旧フィールドを含む設定には `retention_days` への移行方法を示す検証エラーを返す。

### GCSには単一のage ruleを適用する

GCS bucket には全 object を対象とする `age = retention_days` の削除 lifecycle だけを設定し、`cas/v1/` に一致する 1 日 rule を除去する。すべての GCS データを同じ期間保持するため、commit 状態を区別する object key の変更や metadata 追加は行わない。

### Firestoreの期限基準は既存契約を維持する

セッション、run、イベントが現在使用する基準時刻から `retention_days` を加算して `expires_at` を保存する。GCS はサービス仕様に合わせ object 作成時刻から age を評価するため、Firestore と完全に同時刻に消えることではなく、同じ設定日数を使用することを契約とする。

### 既存データの期限は延長しない

この変更は既定30日を維持するため、Firestore の既存 `expires_at` を書き換えない。GCS では 1 日 rule を除去しても、既に削除された object は復元されない。残存 object は 30 日 rule の対象となる。

## Risks / Trade-offs

- [一時 object も30日保持されストレージ使用量が増える] → plan 前に対象と日数を表示し、環境単位で共通 `retention_days` を短縮可能にする。
- [旧リリース設定が読み込めなくなる] → schema version と検証エラーで新しい `retention_days` への移行方法を明示する。
- [1日 rule により既に削除されたGCS objectは戻らない] → 変更後の lifecycle が将来の object に正しく適用されることを確認し、過去データの自動復元は扱わない。
- [非同期削除により期限後も物理データが残る] → UI/API では Firestore の期限判定を継続し、運用文書に削除遅延を明記する。

## Migration Plan

1. リリース設定の schema version を更新し、旧3フィールドを `retention_days: 30` へ置き換える。
2. アプリケーションと Cloud Run Job が共通値を Firestore の `expires_at` へ使用する状態を配備する。
3. Terraform plan で GCS の `cas/v1/` 1 日 rule が除去され、全 object 対象の 30 日 rule だけになることを確認して適用する。
4. Firestore の TTL policy、Job の環境変数、GCS lifecycle の実効値を実環境で確認する。
5. 問題時は設定と lifecycle を直前の状態へ戻す。既に削除されたデータは lifecycle のロールバックでは復元されない。
