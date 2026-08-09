## 1. Runtime契約の追加

- [x] 1.1 system prompt、model、allowed tools を保持する不変な Agent 設定型と、snapshot 容量・質問 timeout・SDK version などを保持する runtime policy 型を追加する
- [x] 1.2 初回のみの workspace initializer と全 run で実行する workspace setup の型付き hook 契約を追加する
- [x] 1.3 Agent 設定の validation、既定 policy、新規/resume の両方への option 適用を検証する unit test を追加する

## 2. Claude Agent SDK境界の整理

- [x] 2.1 現在の SDK query、option 構築、イベント正規化、質問 broker を非公開 session executor として分離する
- [x] 2.2 system prompt と allowed tools を SDK option へ安全に変換し、framework 管理の cwd、HOME、permission callback、resume option をアプリケーションから上書きできないようにする
- [x] 2.3 resume session ID に対応する transcript 探索、必要な cwd 対応付け、session store 構成を adapter 内部の共通処理へ移す
- [x] 2.4 transcript がない場合、異なる一時 workspace、同名衝突、session store 読み込み、および新規実行を検証する unit test を追加する

## 3. 永続ジョブlifecycleの統合

- [x] 3.1 claim、prompt/session 取得、snapshot 復元、workspace hooks、SDK event 永続化、snapshot 作成、終端 commit、cleanup を順序どおり実行する `ClaudeAgentAdapter` の単一ジョブ API を実装する
- [x] 3.2 SDK stream の結果を正常終了・cancelled・timed_out・failed と付随情報で表す内部結果型へ統一し、`RunState.RUNNING` の一時結果利用と文字列比較を除去する
- [x] 3.3 正常終了と質問 timeout で重複している snapshot path/version/容量処理を一つにし、失敗・cancel・timeout ごとの保存方針を共通終端処理へ集約する
- [x] 3.4 claim 後の復元、setup、SDK、イベント永続化、snapshot、commit の各失敗が成功確定されず、可能な場合に failed へ確定されることを検証する
- [x] 3.5 重複実行が復元/setup 前に終了すること、新規と resume で setup が各一度だけ呼ばれること、全終了経路で一時 directory が削除されることを end-to-end test で検証する

## 4. Composition APIとサンプルの簡素化

- [x] 4.1 ジョブ環境変数の parse/validation、logging、Firestore/GCS client、Store、runtime policy を構成する Google Cloud composition API を追加する
- [x] 4.2 `example/agent/runtime.py` を Agent 設定、冪等な workspace setup、Store/composition の初期化、単一ジョブ API 呼び出しだけに書き換える
- [x] 4.3 サンプルから run/session の取得、Store state 更新、snapshot の get/create/extract、`relocate_claude_transcript`、成功・失敗 commit、重複する timeout/version 定数を削除する
- [x] 4.4 core module が Google SDK を import しないこと、およびサンプル runtime に禁止した Store/snapshot/lifecycle 操作が戻らないことを境界テストで検証する

## 5. 互換性・文書・品質確認

- [x] 5.1 リポジトリ内の `ClaudeAgentAdapter.run/events` と `JobRunner` の直接利用を新 API または非公開 test seam へ移行し、必要な場合だけ期限を明示した互換 wrapper を追加する
- [x] 5.2 README とサンプル説明を Agent 設定、毎回の workspace setup、共通 lifecycle の責務境界に合わせて更新する
- [x] 5.3 `pytest`、`mypy`、`ruff` を実行し、新規・resume・成功・timeout・cancel・例外の回帰がないことを確認する
