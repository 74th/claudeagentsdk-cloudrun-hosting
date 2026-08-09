## Context

現在の `example/agent/runtime.py` は composition root であると同時に application service としても動作し、`ChatStore` から session/run を読み、GCS snapshot を展開し、SDK adapter を起動し、結果に応じて snapshot と終端状態を commit している。一方、`ClaudeAgentAdapter` は SDK option・イベント正規化・質問 broker を、`JobRunner` は claim・イベント永続化・終端 commit を担当しており、1 run の lifecycle が三箇所に分散している。

Claude Agent SDK 0.2.128 を前提とし、Firestore/GCS のデータ形式、provider-neutral な Store port、Cloud Run Job の `RUN_ID` と実行 identity は維持する。動機は `proposal.md`、外部から観測できる契約は各 delta spec を参照する。

## Goals / Non-Goals

**Goals:**

- サンプル利用者が編集する Agent 設定と workspace setup を、永続 lifecycle から型とファイル構造で分離する。
- 1 run の claim から終端 commit までを一つの公開操作としてテスト可能にする。
- 新規と resume、成功と timeout/失敗で共通処理が分岐・重複しない構造にする。
- Claude Agent SDK 固有の transcript・session store・option 構築を adapter 境界から漏らさない。

**Non-Goals:**

- ChatStore、WorkspaceStore、Firestore/GCS schema、snapshot archive 形式を変更しない。
- Claude Agent SDK 以外の Agent provider を同じ runtime に抽象化しない。
- Control plane、ExecutionBackend、UI の会話モデルを変更しない。
- workspace setup に任意の migration 機構や version 管理を追加しない。

## Decisions

### 1. `ClaudeAgentAdapter`を1 runの公開facadeにする

公開する `ClaudeAgentAdapter` は `ChatStore`、`WorkspaceStore`、Agent 設定、runtime policy、workspace hooks を受け取り、`run_job(invocation) -> int`（非同期本体）で lifecycle 全体を実行する。現在の SDK query とイベント正規化は内部の session executor へ分離し、`JobRunner` の claim・永続化・commit ロジックは facade からだけ呼ぶ内部協調オブジェクトにする。

これにより「adapter は SDK メッセージ変換だけ」という狭い定義から、「Claude Agent SDK を永続ジョブモデルへ適合させる境界」という定義へ揃う。別案の新しい `HostedAgentRuntime` を公開して既存 adapter を残す構成は名前が二つ並び、サンプル利用者がどちらを使うか再び判断する必要があるため採用しない。低水準 API は unit test 用の非公開部品に下げる。

実行順は次の一方向に固定する。

```text
invocation検証 → claim → prompt/session取得 → 一時directory作成
  → snapshot復元 または 初回initializer
  → 毎回workspace setup
  → transcript resume準備 → SDK実行/イベント永続化
  → snapshot作成 → success/timeout/failure commit → cleanup
```

claim を復元より先に行い、重複 Job が GCS download や workspace setup まで進まないようにする。commit と cleanup は facade が全経路で統括し、アプリケーション callback は Store を受け取らない。

### 2. Agent設定を不変値として分離する

`ClaudeAgentConfig` 相当の不変設定値に少なくとも `system_prompt`、`model`、`allowed_tools` を持たせる。adapter はこれを Claude Agent SDK option へ変換し、`cwd`、`HOME`、permission callback、resume、session store など framework 管理 option を追加する。

Agent 設定から任意の SDK option dict を直接注入する方式は、アプリケーションが `cwd` や `session_store` を上書きして永続化保証を壊せるため採用しない。将来追加する option も、Agent の振る舞いを変えるものと lifecycle の安全性を担うものを明示的に分類する。

tools は SDK が受理する built-in tool 名または tool 定義の読み取り専用 collection とし、質問連携に必要な framework callback と競合しない形で合成する。`AskUserQuestion` を許可した場合も質問 broker の構成はアプリケーションへ露出させない。

### 3. 初回initializerと毎回setupを別hookにする

- `workspace_initializer(workspace)`: committed snapshot がない場合だけ、空 workspace に対して呼ぶ。
- `workspace_setup(workspace)`: initializer または snapshot 復元の後、全 run で一度呼ぶ。

setup は repository checkout の補正、実行時設定ファイルの生成、run ごとに必要な依存準備を想定する。復元前に実行すると snapshot に上書きされるため、必ず復元後とする。setup の変更は次の成功 snapshot に含まれる。hook が失敗した場合は SDK を起動せず failed とし、部分的な workspace を成功 snapshot として commit しない。

既存 `prepare_workspace*` は「復元または初期化」を担当する primitive として残し、facade がその直後に setup を一度だけ呼ぶ。二つの helper に setup 引数を重複追加する案は、呼び出し側によって実行回数が変わりやすいため採用しない。

### 4. transcript再開をadapter内部へ移す

復元後、resume が指定された場合に adapter 内部の一つの helper が次を行う。

1. 復元済み `.claude/projects` から session ID に対応する JSONL を探索する。
2. SDK の cwd 由来パスが必要な版では現在 workspace の key へ安全に対応付ける。
3. path-independent な `session_store` も設定して保存済み会話を SDK へ渡す。

既存の `relocate_claude_transcript` はサンプルから削除し、session ID を使わず全 JSONL をコピーする実装をそのまま公開 helper として残さない。互換対応として複製が必要なら対象 session のみに限定し、同一ファイル判定と衝突時検証を内部で行う。SDK 更新で cwd 対応付けが不要になった場合は内部実装だけを除去できる。

### 5. compositionとstate操作の境界を明示する

Google Cloud 用の factory は環境変数の parse/validation、Firestore client、GCS client、`FirestoreChatStore`、`GCSWorkspaceStore`、runtime policy を構成した値を返す。`example/agent/runtime.py` はこの factory を呼ぶか Store を明示的に生成してよいが、`get_run_for_job`、`get_session`、`append_event`、`commit_terminal`、snapshot の `get/create` は呼ばない。

サンプルの構造は概念的に次の三点だけにする。

```text
AGENT = ClaudeAgentConfig(system_prompt=..., model=..., allowed_tools=...)
setup_workspace(path) -> None
main() -> create_runtime(...).run_from_environment(AGENT, setup_workspace)
```

logging 初期化、retention/timeout/snapshot size の validation、SDK version 解決は共通 factory/policy へ移す。秘密値や prompt は引き続き環境変数へ載せず Store から取得する。

### 6. lifecycle結果を一つの内部結果型で扱う

SDK event stream の終了結果を `RUNNING` という一時的な `RunState` や文字列比較で表現せず、内部の実行結果型で「正常終了」「cancelled」「timed_out」「failed」と、最終出力・Claude session ID・snapshot 保存要否を表す。facade は結果型から一度だけ終端処理を選ぶ。

これにより現在の question timeout 専用 snapshot 作成と正常時 snapshot 作成の重複、`state.value == "running"`、例外経路ごとの commit 呼び分けを除去できる。公開 Store model の `RunState` 自体は変更しない。

## Risks / Trade-offs

- [高水準 facade に責務が集中して巨大化する] → SDK session、workspace lifecycle、durable run coordinator を非公開の小さな協調オブジェクトに分け、公開入口だけを一つにする。
- [既存の `ClaudeAgentAdapter.run/events` 利用コードが壊れる] → リポジトリ内利用箇所を一括移行し、必要なら一リリースだけ明示的な deprecated wrapper を置く。サンプルと文書は新 API のみを示す。
- [毎回 setup が非冪等で resume workspace を破壊する] → hook 契約に毎回実行を明記し、サンプル setup を冪等にする。失敗時は Agent と成功 commit を行わない。
- [transcript の内部再配置で同名 JSONL が衝突する] → resume session ID に対象を限定し、異なる内容を無条件に上書きせずエラーとして扱うテストを追加する。
- [claim を早めることで復元失敗した run が running になる] → claim 後の全例外を共通 failure commit へ通し、復元エラーコードを保持する。
- [Store factory が Google Cloud 固有設定を core へ漏らす] → provider-neutral な facade constructor と Google Cloud composition module を分離し、core module は Google SDK を import しない境界テストを維持する。

## Migration Plan

1. Agent config、runtime policy、workspace setup の契約と unit test を追加する。
2. Claude SDK session executor に system prompt/tools と transcript resume 内部処理を実装する。
3. claim から終端 commit までの coordinator を facade 配下へ統合し、新規・resume・成功・timeout・cancel・例外の end-to-end test を追加する。
4. Google Cloud composition API を追加し、`example/agent/runtime.py` を新しい宣言的入口へ移行する。
5. サンプルから Store state 操作、snapshot 操作、`relocate_claude_transcript`、重複 policy parsing を削除し、構造を検査する境界テストを肯定条件と禁止条件の両方で更新する。
6. README とコンテナ test を更新し、全 unit test、type check、lint を通してから低水準公開 API の互換 wrapper を削除する。

ロールバック時は facade の採用箇所を旧 composition へ戻せるが、Firestore/GCS schema と snapshot 形式は変えないためデータ migration は不要とする。
