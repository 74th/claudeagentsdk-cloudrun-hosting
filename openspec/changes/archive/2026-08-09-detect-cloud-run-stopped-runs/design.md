## Context

現在の制御クライアントにはRun IDと保存済みExecution参照を使う`reconcile`操作があり、Cloud Run JobsバックエンドもExecutionの状態を`ExecutionState`へ正規化できる。一方、Streamlitのactive run更新ループからreconcileが呼ばれておらず、JobがFirestoreへ終端状態を書けずに停止するとRunがactiveのまま残る。FirestoreとCloud Runの両方を正本にせず、Firestoreをアプリケーション状態の正本、Cloud Runを実行状態の観測元として扱う。

## Goals / Non-Goals

**Goals:**

- Run IDから保存済みExecutionを定期的に照会し、Cloud Runの停止・失敗をRunへ反映する。
- 一時的なAPI障害、並行reconciler、再照会を安全に扱う。
- Streamlitの既存自動更新で異常終了を利用者へ知らせ、active runを解放する。
- 外部ポーラーからも同じControlClient操作を呼び出せるprovider-neutralな境界を保つ。

**Non-Goals:**

- Cloud Scheduler、Cloud Tasks、常駐監視サービスなどの新しい実行基盤を追加しない。
- Cloud Run Executionログ本文やGoogle Cloud固有の詳細エラーをFirestoreへ保存しない。
- ExecutionがまだpendingまたはrunningのRunをタイムアウトで自動失敗させる機能は追加しない。
- Jobコンテナが正常にFirestoreへ完了を書き込む既存ライフサイクルを置き換えない。

## Decisions

### 1. 既存のreconcile APIを定期更新から呼び出す

`ControlClient.reconcile(run_id, holder)`をRun ID単位の唯一の制御操作として維持し、Streamlitのactive run更新サイクルで呼び出す。UIはprovider SDKへ直接アクセスせず、`ChatViewModel`にreconcile操作を公開する。Cloud Schedulerで別サービスを作る案は、デプロイ対象と認証境界を増やし、画面を開いていないRunの監視方針まで別途必要になるため採用しない。将来の外部ポーラーは同じControlClient契約を再利用できる。

### 2. Run IDはFirestoreからExecution参照へ解決する

再照会の入力はRun IDとし、ControlClientがFirestoreからRunを取得して保存済みの`ExecutionReference`をExecutionBackendへ渡す。Cloud Run APIのExecution名をRun IDから推測する方式は、Execution名がRun IDを含む保証がなく、実行作成直後に参照がOperation名になる場合もあるため採用しない。

### 3. 状態取得エラーを終端状態と分離する

Cloud RunのCompleted条件が失敗または停止を示す場合はExecutionBackendがfailedまたはcancelledへ正規化し、ControlClientが安全なエラーコード付きでFirestoreへ反映する。Executionが見つからない場合は実行消失として非再試行エラーにする。一方、quota、deadline、unavailableなどの一時的エラーはControlClientがRunを変更せず、その更新サイクルを終了して次回照会へ委ねる。

### 4. Firestoreの終端更新は既存leaseと状態保護を拡張する

既存のreconciliation leaseを使い、Run、Sessionのactive run参照、latest state、finished_at、保持期限を一つの終端更新境界で更新する。終端Runへの遅延したJob書き込みや再照会がRunを復活させないよう、ChatStoreの更新は現在状態を確認してから適用し、既存のterminal transition保護を再利用する。エラーコードはprovider詳細を含まない固定値（例: `cloud_run_execution_failed`、`cloud_run_execution_not_found`）に限定する。

### 5. UIは終端検知後に最新Runを再取得する

active runを描画する前にreconcileを実行し、結果が終端へ変わった場合はSessionとRunを再取得して失敗状態を表示する。pendingまたはrunningなら従来どおり自動更新を継続する。reconcile呼び出しの一時的な例外はUI全体を落とさず、次の更新へ持ち越す。これによりJob側のFirestore書き込みが失敗した場合でも、Run IDとCloud Run execution IDを含む診断可能な表示を維持する。

## Risks / Trade-offs

- [画面が開かれていない間はUIポーリングが実行されない] → 外部ポーラーから同じRun ID reconciliation APIを呼び出せる構造にし、常時監視が必要になった場合は別変更でスケジューラを追加する。
- [Cloud Run API照会がRunごとに発生する] → active runだけを対象にし、既存の更新間隔で一度だけ照会する。
- [Execution消失を失敗と判定する可能性がある] → 取得不能の一時エラーとNotFoundをエラー型で区別し、NotFoundのみ実行消失として終端化する。
- [Jobとreconcilerが同時に終端更新する] → Firestore leaseとterminal state guardを併用し、後着更新が既存の成功・失敗状態を上書きしないようにする。

## Migration Plan

1. Execution状態、Firestore終端反映、UI定期照会の順に互換性を保ったコードを配備する。
2. UIを再起動し、active runのpending、成功、失敗、キャンセル、NotFoundをテスト環境で確認する。
3. Cloud Run JobがFirestoreへ完了を書き込めない場合でも、次回UI更新でRunが終端化されることを確認する。
4. ロールバック時はUIからreconcile呼び出しを外しても、既存のRunとJobライフサイクルは維持できる。reconcilerが既に保存した終端状態はアプリケーションロールバックでactiveへ戻さない。
