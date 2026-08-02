# Firestore live 契約 fixture（opt-in）

- 実行日: 2026-08-02
- project / database / location: `nnyn-dev` / `(default)` / `us-central1`
- database type: Firestore Native、Standard、Pessimistic concurrency
- SDK: `google-cloud-firestore 2.28.0`（この probe 用。固定依存はタスク 1.7 で決定する）
- 検証データ: `openspec_contract_probe/run-20260802`（probe の finally で再帰削除）

`sessions` の `(updated_at DESC, session_id DESC)` query は複合 index なしで
`FAILED_PRECONDITION` になった。`sessions` collection group の同順 index を作成して
`READY` 後に下記を再実行した。この index はタスク 7.2 で Terraform 管理へ移す。

```json
{
  "transaction_retry": {"value": 2, "attempts": [1, 2]},
  "conditional_claim": {"winners": ["owner-a"], "stored_owner": "owner-a"},
  "cursor_pagination": {
    "first": ["session-b", "session-a"],
    "second": ["session-c"]
  },
  "listener": {
    "initial": true,
    "changed": true,
    "persisted_after_disconnect": ["event-1"],
    "reconnected": true,
    "snapshots": [[], ["event-1"], ["event-1"]]
  }
}
```

## 確定した契約

- Firestore transaction は競合時に同じ callback を再実行する。run ID、event ID、
  idempotency key などの外部識別子は transaction 外で一度だけ発行する。
- 条件付き claim は transaction 内で所有者不在を確認して更新すれば single winner にできる。
- cursor は `updated_at` だけでなく `session_id` も含める。両方を index と query の
  順序に一致させなければ、同時刻のセッションで安定したページングにならない。
- listener は空集合の初回 snapshot、write 後の snapshot、再購読時の既存 event snapshot
  を配信した。UI は event ID と `(sequence, event_id)` による重複除去を必須とする。

