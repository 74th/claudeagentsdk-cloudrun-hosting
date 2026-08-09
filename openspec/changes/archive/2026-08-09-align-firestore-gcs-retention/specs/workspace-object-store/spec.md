## MODIFIED Requirements

### Requirement: snapshot容量と保持期間を制限する
システムは圧縮前後の snapshot 容量上限と、未 commit および commit 済みオブジェクトに共通する保持期間を設定可能にし、上限超過 snapshot を有効版として保存してはならない（MUST NOT）。workspace、transcript、一時 object の共通保持期間は作成から既定 30 日とし、Firestore チャットデータと同じリリース設定値を使用しなければならない（SHALL）。commit 状態または object prefix によって共通保持期間より短い削除 lifecycle を適用してはならない（MUST NOT）。

#### Scenario: snapshotが容量上限を超える
- **WHEN** snapshot の圧縮前または圧縮後容量が設定上限を超える
- **THEN** システムは snapshot を commit せず容量超過エラーを返す

#### Scenario: GCS objectが既定保持期間へ到達する
- **WHEN** workspace、transcript、または一時 object が作成から 30 日へ到達する
- **THEN** オブジェクトストアは commit 状態にかかわらず当該 object を自動削除の対象にする

#### Scenario: 設定した保持期間をGCSへ適用する
- **WHEN** リリース設定で有効な共通保持期間を指定する
- **THEN** workspace、transcript、および一時 object はすべて作成から指定日数後に自動削除の対象となる
