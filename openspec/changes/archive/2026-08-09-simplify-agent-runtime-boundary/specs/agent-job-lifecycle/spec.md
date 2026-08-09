## ADDED Requirements

### Requirement: フレームワークが永続lifecycleを所有する
フレームワークの共通実行境界は run の所有権取得、prompt と session の読み出し、resume 判定、イベント永続化、snapshot の保存、および終端状態の確定を一貫して所有しなければならない（SHALL）。アプリケーション固有の Agent runtime に Store の状態遷移を要求してはならない（MUST NOT）。

#### Scenario: resume可能なrunを共通境界で実行する
- **WHEN** committed snapshot と Claude session ID を持つ session の後続 run を起動する
- **THEN** 共通実行境界は Store から必要な状態を取得し、復元と Agent 実行を行い、結果と新しい snapshot を終端状態へ確定する

#### Scenario: Agent実行中に例外が発生する
- **WHEN** workspace setup、Claude Agent SDK、イベント保存、または snapshot 保存が失敗する
- **THEN** 共通実行境界は成功状態を確定せず、可能な範囲で失敗状態を保存して失敗終了コードを返す

### Requirement: Claude transcript再開処理を共通化する
システムは復元済み Claude transcript を現在の一時 workspace から再開可能にする Claude Agent SDK 固有処理を共通 adapter 内で実行しなければならない（SHALL）。アプリケーションに transcript ファイルの探索、複製、または session store の構成を要求してはならない（MUST NOT）。

#### Scenario: 一時workspaceパスが変わる
- **WHEN** resume 対象 snapshot の作成時と現在の Job で workspace の絶対パスが異なる
- **THEN** 共通 adapter は保存済み Claude session ID の transcript を SDK が解決可能な形で提供し、アプリケーション固有コードを介さず会話を再開する
