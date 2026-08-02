# Agent Platform 契約 PoC

このディレクトリは `add-gemini-enterprise-hosting-adapter` の実サービス契約を固定・再検証するための PoC 環境である。

## 固定条件

- Google Cloud project: `nnyn-dev`
- Agent Platform location: `us-central1`
- Agent display name: `test-claude-agent-sample`
- Claude model: `claude-haiku-4-5@20251001`
- Claude Agent SDK: `0.2.128`
- Google Cloud Vertex AI / Agent Platform SDK: `1.163.0`
- Google Cloud SDK CLI: `578.0.0`

依存は `uv lock` で解決し、実サービスへの課金対象テストは明示的な環境変数を設定した場合だけ実行する。各 request/response は秘密情報を除外して `poc/fixtures/` に保存する。

```bash
uv sync --directory poc
RUN_LIVE_GCP_POC=1 uv run --directory poc python -m pytest -m live_gcp
```

PoC は通常・streaming・async/cancel、Session Store、GCS snapshot、Gateway の各契約を確認する。実行後のリソース名、生成された Operation、ログ観測時刻を fixture に記録する。

## 2026-08-01: async dispatcher の切り分け結果

`us-central1` の最小 echo コンテナで、通常 query はコンテナへの `POST /api/reasoning_engine` と応答を確認した。一方、次のいずれの long-running job も `RUNNING` のままコンテナへ POST されず、出力は入力オブジェクトだけだった。

- SDK の `run_query_job` で inline `query` を渡す経路
- `query` と `async` の class method 定義をそれぞれ使う経路
- REST `:asyncQuery` で `input_gcs_uri` / `output_gcs_uri` を渡す経路

REST 経路の再現 Operation は
`projects/nnyn-dev/locations/us-central1/operations/860433308331278336`。
受理後 30 秒で `check_query_job(..., retrieve_result=True)` は `RUNNING`、対象 Reasoning Engine
`projects/nnyn-dev/locations/us-central1/reasoningEngines/565256179060572160` のログは 0 件だった。診断ジョブには正しい SDK 契約
`cancel_query_job(name=<reasoning-engine>, config={"operation_name": <operation>})`
で取消要求を送ったが、25 秒後も `RUNNING` のままで、出力 GCS object は生成されなかった。

この時点で確認済みの前提は次のとおり。

- サービスエージェント `service-776113568960@gcp-sa-aiplatform-re.iam.gserviceaccount.com` に
  `roles/aiplatform.reasoningEngineServiceAgent` が付与されている。
- `gs://nnyn-dev-agent-hosting-poc-us-central1` に同サービスエージェントの
  `roles/storage.objectCreator` と `roles/storage.objectViewer` が付与されている。
- バケットは `US-CENTRAL1` にある。
- `asia-northeast1` では同じイメージをリージョン内 Artifact Registry へ複製しても
  Reasoning Engine の起動自体が `failed to start and cannot serve traffic` で失敗した。

従って、アダプターの async payload、コンテナ実装、class method 定義、入力・出力 GCS
権限だけでは説明できない。次に確認する対象は VPC Service Controls と組織ポリシー、
および Agent Platform サポートで参照できる dispatcher 側ログである。プロジェクトには
Organization Policy API が未有効化のため、組織ポリシーは現時点で read-only に列挙できない。
