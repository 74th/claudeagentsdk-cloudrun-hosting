## Purpose

アプリケーション固有の Claude Agent 設定と run ごとの workspace 準備だけを宣言し、永続化 lifecycle の詳細を再実装せず安全にジョブを起動できる境界を提供する。

## ADDED Requirements

### Requirement: Agentの追加設定を宣言できる
アプリケーションは Claude Agent の system prompt、model、および利用可能な tools を runtime 設定として宣言できなければならない（SHALL）。指定した設定は新規実行と resume 実行の両方へ一貫して適用されなければならない（SHALL）。

#### Scenario: Agent固有設定でrunを実行する
- **WHEN** アプリケーションが system prompt、model、tools を指定して run を起動する
- **THEN** Claude Agent は指定された設定と、フレームワークが付加する安全な共通設定を用いて実行される

### Requirement: 単一のruntime APIでジョブを起動する
アプリケーションは構成済み Store、Agent 設定、および workspace setup を渡す単一の runtime API でジョブを起動できなければならない（SHALL）。アプリケーションは run、session、event、snapshot の永続状態を直接遷移させなくても正常・異常の全実行経路を完了できなければならない（SHALL）。

#### Scenario: アプリケーションruntimeからrunを開始する
- **WHEN** アプリケーションが永続 Store を構成して runtime API を呼び出す
- **THEN** runtime API は永続要求の取得から終端状態の確定までを実行し、プロセスの終了コードを返す

### Requirement: Agent設定と共通policyを分離する
システムはアプリケーション固有の Agent 設定と、質問 timeout、snapshot 容量上限、SDK version、logging などの共通 runtime policy を別々に構成可能にしなければならない（SHALL）。共通 policy の既定値は新規実行と resume 実行で一致しなければならない（MUST）。

#### Scenario: 共通policyの既定値を使用する
- **WHEN** アプリケーションが Agent 設定だけを指定してジョブを起動する
- **THEN** システムは一元管理された共通 policy の既定値を適用し、アプリケーションruntimeに lifecycle 定数の複製を要求しない
