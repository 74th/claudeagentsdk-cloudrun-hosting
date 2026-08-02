# Cloud Run Jobs API 契約 fixture（opt-in）

- 実行日: 2026-08-02
- project / region: `nnyn-dev` / `us-central1`
- 検証用 Job: `test-claudesdk-cloudrun`
- CLI: `gcloud 578.0.0`
- イメージ: `us-docker.pkg.dev/cloudrun/container/job:latest`

この fixture は明示された検証用 project でだけ再実行する。Job コンテナへは
`RUN_ID` と非秘密の設定だけを渡し、会話本文・認証情報は渡さない。

## Job 作成 request と設定 response

```sh
gcloud run jobs create test-claudesdk-cloudrun \
  --project nnyn-dev --region us-central1 \
  --image us-docker.pkg.dev/cloudrun/container/job:latest \
  --task-timeout 600s --max-retries 0 \
  --set-env-vars RUN_ID=placeholder \
  --labels purpose=openspec-contract-poc
```

作成後の Job は `spec.template.spec.timeoutSeconds=600`、
`maxRetries=0`、実行 identity
`776113568960-compute@developer.gserviceaccount.com` を返した。実運用では
専用の job service account を指定し、この既定 identity を使わない。

## Execution 開始、run ID override、状態取得

```sh
gcloud run jobs execute test-claudesdk-cloudrun \
  --project nnyn-dev --region us-central1 \
  --update-env-vars RUN_ID=7adcfb4f-a913-4724-874a-f525542daf46 \
  --async --format=json
```

開始 response の要点は次のとおりである。

```json
{
  "metadata": {
    "name": "test-claudesdk-cloudrun-njbdb",
    "uid": "5495f69c-0650-4e91-bd41-a2634b2c9e33",
    "annotations": { "run.googleapis.com/operation-id": "f6ee2360-e3ea-4a90-abfa-f5ecc7c6652c" }
  },
  "spec": {
    "template": {
      "spec": {
        "containers": [{"env": [{"name": "RUN_ID", "value": "7adcfb4f-a913-4724-874a-f525542daf46"}]}],
        "maxRetries": 0,
        "timeoutSeconds": "600"
      }
    }
  }
}
```

`metadata.name` が backend に保存する execution reference であり、コンテナ側は
override された `RUN_ID` から Firestore の run を取得する。状態取得は次で行う。

```sh
gcloud run jobs executions describe test-claudesdk-cloudrun-njbdb \
  --project nnyn-dev --region us-central1 --format=json
```

開始前は `Completed.status=Unknown` および `Started.status=Unknown` だった。
実行開始後は `Started.status=True` になった。adapter は condition の型と status を
正規化し、`Completed=False, reason=Cancelled` を `cancelled` と扱う。

## cancel と終端状態

```sh
gcloud run jobs executions cancel test-claudesdk-cloudrun-njbdb \
  --project nnyn-dev --region us-central1 --quiet --format=json
```

cancel コマンドの response body は空配列だったため、停止完了は describe response で
確認する。終端 response の要点は次のとおりである。

```json
{
  "conditions": [
    {"type": "Completed", "status": "False", "reason": "Cancelled", "message": "Cancelled by user."},
    {"type": "Started", "status": "True", "message": "Started deployed execution in 31.93s."}
  ],
  "startTime": "2026-08-02T08:04:27.580633Z",
  "completionTime": "2026-08-02T08:04:48.107048Z"
}
```

## 再実行手順

1. `RUN_ID` に新しい UUID を発行する。
2. 上記の `execute --update-env-vars RUN_ID=<UUID> --async` を実行する。
3. `executions describe` で execution reference と condition を採取する。
4. 実行中に `executions cancel --quiet` を実行し、describe が `Completed=False` /
   `reason=Cancelled` を返すことを確認する。

## 重複ディスパッチの観測（タスク 1.3 の途中結果）

同じ `RUN_ID=40fb7512-a527-40ca-a8ef-0615d52fb367` で `execute --async` を連続して
2 回呼び出したところ、Cloud Run はどちらも受け付け、別々の Execution を返した。

```json
{
  "execution_names": [
    "test-claudesdk-cloudrun-9jb69",
    "test-claudesdk-cloudrun-6vhph"
  ],
  "operation_ids": [
    "18bbca19-30f2-49e9-b260-136d750d8e08",
    "63cc339c-9008-4c32-8be1-97c4f97ab27f"
  ]
}
```

両 Execution の cancel 後は `Completed=False` / `reason=Cancelled` になった。cancel
コマンド完了までの観測値は各 10,495 ms と 21,728 ms である。これは control-plane の
cancel 完了時間であり、コンテナが SIGTERM を受信して終了する猶予時間そのものではない。

## SIGTERM と制御プロセス停止の観測

`execute --async` は Execution 名を返して制御プロセスを直ちに終了する。Execution
reference の保存前にその制御プロセスが停止しても、後続の同一 run ID dispatch が可能で
あることは、上記の重複 Execution で確認した。

`busybox:1.36.1` を次の trap 付き command で一時的に使用した。

```sh
sh -c 'trap "echo SIGTERM_RECEIVED; sleep 5; echo SIGTERM_FINISHED; exit 0" TERM; echo READY; while true; do sleep 1; done'
```

Cloud Logging は Execution 状態より遅れて到着したが、同一 Execution で次の順序を記録
した。

| Execution | ログ | 時刻 |
| --- | --- | --- |
| `test-claudesdk-cloudrun-ctt8f` | `SIGTERM_RECEIVED` | `2026-08-02T08:11:09.920946Z` |
| 同上 | `SIGTERM_FINISHED` | `2026-08-02T08:11:14.920528Z` |
| `test-claudesdk-cloudrun-tfhd7` | `SIGTERM_RECEIVED` | `2026-08-02T08:14:02.643960Z` |
| 同上 | `SIGTERM_FINISHED` | `2026-08-02T08:14:07.644176Z` |

したがって、この検証コンテナは SIGTERM 受信後に 5 秒の協調終了処理を完了できた。
adapter / JobRunner は SIGTERM 時にこの猶予より十分短い処理だけを試み、成功状態を
記録しない。Cloud Logging の遅延を状態判定に用いてはならない。
