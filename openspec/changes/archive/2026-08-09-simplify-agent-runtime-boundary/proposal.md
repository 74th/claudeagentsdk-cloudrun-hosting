## Why

`example/agent/runtime.py` が Agent 固有設定だけでなく、永続状態の取得・更新、workspace snapshot の復元と保存、Claude transcript の再配置、timeout、run の終端確定まで直接実装しており、サンプル利用者が変更すべき箇所とフレームワークの共通処理が判別しにくい。Claude Agent SDK を前提とする共通 lifecycle をフレームワークへ集約し、アプリケーション側の runtime を Agent の追加設定と毎回の workspace setup に限定する。

## What Changes

- Agent 作成時に system prompt、model、利用可能な tools を宣言できる、アプリケーション向けの設定契約を提供する。
- snapshot の有無にかかわらず、復元完了後かつ Agent 実行前に毎回呼ばれる workspace setup hook を提供し、初回だけ実行する既存 initializer と意味を分離する。
- run claim、prompt 読み出し、resume 判定、イベント永続化、質問 timeout、snapshot 保存、成功・失敗・キャンセルの終端確定を、フレームワーク側の単一のジョブ実行 API に集約する。
- Claude transcript の可変 workspace パスへの対応付けと session store による復元を Claude Agent SDK adapter の内部処理にし、サンプルから `relocate_claude_transcript` を除去する。
- Firestore/GCS の生成と環境変数検証を再利用可能な composition API へまとめ、サンプルでは Store の組み立てまでを許容する一方、Store の state を直接読み書きしない構造にする。
- logging、SDK version、snapshot 容量上限、timeout などの共通 runtime policy を一箇所で解決し、正常・異常の全経路を同じ orchestration で扱う。
- `example/agent/runtime.py` と境界テストを、Agent 設定、workspace setup、composition、共通実行 API の呼び出しだけが見える構成へ更新する。
- **BREAKING**: 低水準の `ClaudeAgentAdapter.events()` と `JobRunner` の手動合成をサンプル向けの推奨 API から外し、Agent 設定と lifecycle 実行を分離した新 API を標準入口にする。

## Capabilities

### New Capabilities

- `agent-runtime-configuration`: アプリケーションが Claude Agent の追加設定と run ごとの workspace setup を宣言し、共通 lifecycle を単一 API で起動する契約。

### Modified Capabilities

- `agent-job-lifecycle`: 永続 Store の state 操作、resume、snapshot、終端確定をフレームワークの共通実行境界が所有する要件を追加する。
- `workspace-object-store`: 初回 workspace initializer とは別に、復元済み workspace にも毎回適用する setup hook の順序と失敗時動作を追加する。
- `realtime-chat-sample`: ジョブ用サンプルの runtime が Agent 固有設定と workspace setup に限定され、共通 lifecycle を再実装しない要件へ変更する。

## Impact

- 主な対象は `example/agent/runtime.py`、`cas_hosting_adapter/agent_adapter.py`、`cas_hosting_adapter/job_runner.py`、`cas_hosting_adapter/workspace_store.py`、Google Cloud composition/factory、および対応する unit・end-to-end・境界テスト。
- Claude Agent SDK の option 構築では system prompt と tools の受け渡しを追加し、transcript 復元の実装を adapter 内へ統合する。
- Firestore と GCS のデータモデル、公開 provider port、Terraform resource、および保存済み snapshot 形式は変更しない。
