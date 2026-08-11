## 1. Toleration設定モデル

- [x] 1.1 `key`、`operator`、`value`、`effect` を持つ GKE リリース用 toleration モデルを追加し、許可値、余分なフィールド、`Exists` と `value` の組み合わせを検証するテストを追加する
- [x] 1.2 GKE backend 用の不変な toleration 値オブジェクトと防御的検証を追加し、リリース設定を経由しない不正値も Job 作成前に拒否するテストを追加する
- [x] 1.3 tolerations 未指定を空 tuple として扱い、既存の GKE リリース設定が変更なしで読み込める回帰テストを追加する

## 2. 設定の伝播とJob manifest

- [x] 2.1 リリース設定から `GoogleCloudSettings` と GKE backend へ tolerations を順序どおり変換・伝播し、composition 境界のテストを追加する
- [x] 2.2 GKE Job の Pod テンプレートへ設定済み tolerations の 4 フィールドを順序どおり出力する処理と、複数要素および `operator: Exists` の manifest テストを追加する
- [x] 2.3 tolerations 未設定時は `spec.template.spec.tolerations` 自体を出力しないことを既存 manifest テストで検証する

## 3. リリース例と利用上の注意

- [x] 3.1 `release.gke.yaml` に `operator: Exists`、空の `value`、`effect: NoSchedule` を使う tolerations 設定例をコメント状態で追加する
- [x] 3.2 README の GKE 設定へ tolerations の入力形式、taint を許容するだけで NodePool 選択を単独では保証しない点、検証方法を追記する

## 4. 自動検証とAutopilot実機確認

- [x] 4.1 release config、factory、GKE backend の対象テストと全 pytest、ruff、mypy を実行し、既存実行基盤への回帰がないことを確認する
- [x] 4.2 更新イメージをビルド・push し、`release.gke.yaml` のコメントを一時的に外して `operator: Exists` の toleration を使う GKE Job を Autopilot 検証環境へ作成する
- [x] 4.3 Job が受理された場合は run の Pod を `kubectl get pod -o yaml` で取得して `key`、`operator`、空の `value`、`effect` の反映を確認し、Autopilot に拒否された場合は live test 失敗として API エラーを記録して本番相当環境での再確認事項にする
- [x] 4.4 実機確認後は `release.gke.yaml` の tolerations block を再度コメントアウトし、最終差分と設定読み込みテストで検証専用設定が有効でないことを確認する
