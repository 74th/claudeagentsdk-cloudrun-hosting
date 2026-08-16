# ドキュメント

このディレクトリには、独自のエージェントホスティング基盤として導入・運用するための情報をまとめています。

## 実行基盤を構築する

- [Cloud Run Jobs のセットアップ](setup-cloud-run-jobs.md): 管理対象を減らし、サーバーレスで運用する場合
- [Cloud Batch のセットアップ](setup-cloud-batch.md): run ごとの計算資源を明示して Batch で実行する場合
- [GKE Jobs のセットアップ](setup-gke-jobs.md): 既存の GKE クラスターで Kubernetes Job として実行する場合

実行基盤をまだ決めていない場合は、各ページ冒頭の「この構成を選ぶ場合」を比較してください。Firestore と GCS のデータ層、フロントエンドから利用する API は 3 つの構成で共通です。

## 組み込む・運用する

- [アーキテクチャと Job 運用](architecture-and-job-operations.md): コンポーネントの責務、再訪、キャンセル、障害補正
- [エージェントとフロントエンドのカスタマイズ](customizing-agent-and-frontends.md): サンプルを用途に合わせて変更し、ホスティングする方法
