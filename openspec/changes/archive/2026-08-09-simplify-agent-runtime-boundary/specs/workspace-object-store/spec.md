## ADDED Requirements

### Requirement: runごとにworkspace setupを適用する
システムは新規 workspace の初期化または committed snapshot の復元が完了した後、Agent 実行前に登録済み workspace setup を毎回適用しなければならない（SHALL）。workspace setup は snapshot の有無にかかわらず実行し、初回だけ実行する initializer とは別の契約でなければならない（SHALL）。

#### Scenario: 新規workspaceをセットアップする
- **WHEN** committed snapshot がない run を開始し initializer と workspace setup が登録されている
- **THEN** システムは空の workspace に initializer を適用した後、workspace setup を一度適用してから Agent を開始する

#### Scenario: 復元済みworkspaceをセットアップする
- **WHEN** committed snapshot がある後続 run を開始する
- **THEN** システムは snapshot を復元した後、workspace setup を一度適用してから Agent を resume する

#### Scenario: workspace setupが失敗する
- **WHEN** workspace setup が例外を返す
- **THEN** システムは Agent を開始せず、run を成功扱いせずに一時 workspace を後処理する
