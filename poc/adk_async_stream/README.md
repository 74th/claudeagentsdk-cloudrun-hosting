# ADK `async_stream_query` PoC

この PoC は `agent_framework: google-adk` の source deployment で、ADK が公開する
`async_stream_query` を通常の `streamQuery` と長時間の `asyncQuery` の両方から検証する。

source deployment の entrypoint は `adk_async_stream.agent:root_agent` とする。

検証順序は次のとおりとする。

1. `async_stream_query` を `streamQuery` で呼び出し、SSE event を確認する。
2. 同じ Agent Engine に `run_query_job` を送信する。
3. LRO が runtime に到達して完了し、出力 GCS object が生成されることを確認する。

長時間ジョブの実行結果が成功するまで、この PoC は既存 custom echo PoC の結果を置き換えない。
