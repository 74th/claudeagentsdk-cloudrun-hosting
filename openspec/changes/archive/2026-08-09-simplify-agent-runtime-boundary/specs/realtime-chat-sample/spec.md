## MODIFIED Requirements

### Requirement: ジョブ用サンプルエージェントを提供する
サンプルは Claude Agent SDK エージェントの system prompt、model、tools、run ごとの workspace setup、構成済み Store、および run ID を受け取るジョブエントリーポイントを提供しなければならない（SHALL）。サンプルの Agent runtime は Store の永続状態遷移、workspace snapshot lifecycle、Claude transcript の再配置、または終端 commit を直接実装してはならず（MUST NOT）、これらをフレームワークの共通実行 API に委譲しなければならない（SHALL）。コンテナイメージは非 root で動作しなければならない（SHALL）。

#### Scenario: サンプルジョブを起動する
- **WHEN** 利用者が run ID を与えてサンプルコンテナを起動する
- **THEN** コンテナはサンプルで宣言した Agent 設定と workspace setup を用い、共通実行 API を通じて永続要求の取得、エージェントイベント、および終端状態を保存して終了する

#### Scenario: サンプルのAgent設定を変更する
- **WHEN** 利用者がサンプルの system prompt、model、または tools を変更する
- **THEN** 利用者は Store の読み書き、snapshot、resume、または timeout の lifecycle 実装を変更せず Agent の振る舞いを変更できる
