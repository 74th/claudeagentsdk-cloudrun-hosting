## 1. 実行状態とreconciliation契約

- [x] 1.1 Cloud Run Executionの失敗・キャンセル・消失・一時障害を区別できるエラー／状態モデルと、Firestoreへ保存する固定エラーコードを定義する
- [x] 1.2 `ExecutionBackend`、`ChatStore`、`ControlClient`のreconciliation契約を更新し、Run IDからExecution参照を解決してpending・running・終端状態を返せるようにする
- [x] 1.3 実行中状態を終端化せず、一時的な状態取得エラーではRunを変更しないこと、および終端結果を再照会しても同じ結果になることの契約テストを追加する

## 2. Cloud Run Execution状態の再照会

- [x] 2.1 `CloudRunJobsBackend.get`の状態正規化を失敗・キャンセル・成功・pending・runningの契約に合わせ、Cloud Run APIのNotFoundを実行消失として扱う
- [x] 2.2 quota・deadline・unavailable・ネットワークなどの一時的なCloud Run APIエラーを再試行可能なエラーへ分類し、詳細なプロバイダー payload を永続化しない
- [x] 2.3 Cloud Run状態の正常系、Completed条件の失敗・キャンセル、NotFound、一時障害をfake clientで検証するバックエンドテストを追加する

## 3. ChatStoreの終端反映

- [x] 3.1 `InMemoryChatStore`でreconciliation leaseを取得したRunに対し、終端状態、`finished_at`、固定エラーコード、active run解除、latest state、保持期限を一貫して更新する
- [x] 3.2 `FirestoreChatStore`のトランザクションに同じ終端反映を実装し、Run・Session・leaseの更新競合を安全に処理する
- [x] 3.3 終端Runへの遅延したJob書き込みやpending・running結果の適用が終端状態を復活・上書きしないterminal guardを、両ストアへ適用する
- [x] 3.4 失敗・キャンセル・実行消失・成功後の永続化不整合、重複reconcile、複数reconcilerの同時実行をInMemoryおよびFirestore fakeの契約テストで検証する

## 4. ControlClientのreconciliation

- [x] 4.1 Run IDから所有Sessionと保存済み`ExecutionReference`を解決し、leaseを使ってExecutionを再照会して終端結果をChatStoreへ反映する`ControlClient.reconcile`を完成させる
- [x] 4.2 failed・cancelled・NotFoundをそれぞれ安全な固定エラーコード付きのRun終端状態へ変換し、succeeded時にsnapshot/final永続化が不足している場合は既存の`persistence_failed`へ変換する
- [x] 4.3 pending・runningおよび一時的な状態取得エラーではRunとSessionを変更せず、leaseを適切に解放して次回照会へ委ねる
- [x] 4.4 ControlClientの再照会、終端状態の冪等性、未所有Run・参照欠落・各エラー分類を単体および統合テストで検証する

## 5. Streamlitの定期検知と表示

- [x] 5.1 `ChatViewModel`にprovider SDKへ直接依存しないreconcile操作を公開し、active Runの更新前にRun ID単位で呼び出せるようにする
- [x] 5.2 Streamlitの既存自動更新サイクルへreconciliationを組み込み、pending・runningでは従来の実行中表示を継続し、終端化時はSessionとRunを再取得する
- [x] 5.3 Cloud Runの失敗・キャンセル・消失と安全なエラー情報、Run ID、Execution IDをUIへ表示し、active run解除後に新しいRunを開始できるようにする
- [x] 5.4 reconciliationの一時的な例外でUI全体を停止させず、次回更新で再試行するUI・ViewModelテストを追加する

## 6. 検証と運用確認

- [x] 6.1 実行状態、両ChatStore、ControlClient、sample frontendの単体・統合テストを実行し、既存のJob成功・キャンセル・再訪フローに回帰がないことを確認する
- [x] 6.2 formatter、lint/type check、Terraform format/validate、およびstrict OpenSpec validationを実行する
- [x] 6.3 テスト環境でCloud Run Executionのpending・成功・失敗・キャンセル・NotFoundとFirestore書き込み失敗後の次回UI更新による検知を確認し、画面を開いていないRunは別ポーラーが同じControlClient契約を呼ぶ運用上の前提を記録する
