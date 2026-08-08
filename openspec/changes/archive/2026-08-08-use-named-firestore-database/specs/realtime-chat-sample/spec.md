## ADDED Requirements

### Requirement: サンプルのFirestore databaseを一貫して選択する
Streamlit サンプルとCloud Run Jobは同じ検証済みリリース設定からFirestore database名を取得し、その名前付き database へ接続しなければならない（SHALL）。サンプルは設定不在時または `(default)` 指定時に `(default)` databaseへ暗黙に接続してはならない（MUST NOT）。

#### Scenario: サンプル設定で接続する
- **WHEN** 利用者が `firestore_database: claude-agent-chat` を含むサンプルのリリース設定でUIを起動し、runを開始する
- **THEN** UIと起動されたJobはともに `claude-agent-chat` のsession、run、eventを読み書きする

#### Scenario: database設定が欠ける
- **WHEN** 利用者がFirestore database名を欠く、空の、または `(default)` のリリース設定でUIまたはデプロイを開始する
- **THEN** 処理は接続またはクラウド変更の前に設定エラーとして失敗する
