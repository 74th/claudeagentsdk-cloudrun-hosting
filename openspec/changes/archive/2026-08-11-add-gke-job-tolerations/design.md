## Context

GKE Job の生成経路は、`release.gke.yaml` の `gke` block、検証済みリリースモデル、`GoogleCloudSettings`、GKE backend の内部 Job 設定、Kubernetes Job manifest の順に値を受け渡す。現在の Pod テンプレートには ServiceAccount、restart policy、container、resource だけがあり、スケジューリング用の tolerations はない。

Kubernetes の toleration は taint を許容する条件であり、それ単体で特定 NodePool を選択する条件ではない。特定 NodePool への限定は、対象外 NodePool の taint 構成または node affinity／node selector と組み合わせる運用上の前提になる。今回の Autopilot 検証環境では NodePool と taint を正確に再現できないため、仕様に示したとおり生成済み Pod YAML への反映を検証境界とする。

## Goals / Non-Goals

**Goals:**

- リリース YAML から Job manifest まで、複数の toleration とその順序を型検証付きで伝播する。
- リリース設定を経由しない GKE backend の直接利用でも、不正な toleration を Kubernetes API 呼び出し前に拒否する。
- tolerations 未指定の既存リリースと既存 Job manifest の動作を維持する。

**Non-Goals:**

- NodePool、taint、node affinity、node selector を作成または管理しない。
- toleration によって特定 NodePool への配置を単独で保証しない。
- Autopilot 環境で本番 NodePool のスケジューリング結果を代替検証しない。

## Decisions

### リリース境界とbackend境界の両方でtolerationを検証する

リリース設定には toleration 要素の専用モデルを追加し、`key`、`operator`、`value`、`effect` 以外のフィールドを拒否する。operator と effect は Kubernetes が受理する列挙値に限定し、`Exists` と空でない `value` の組み合わせを相関検証する。GKE backend も同じ不変条件を検査し、テストや他の composition root から直接構築された場合の防御境界にする。

未検証の任意 `dict` をそのまま backend へ渡す案は、タイプミスがクラスタへの Job 作成時まで発見されず、設定エラーと Kubernetes API エラーを区別できないため採用しない。Kubernetes client の生成モデルを公開設定に直接使う案も、SDK 型を設定・共通 composition へ漏らすため採用しない。

### 内部表現は不変なtoleration値オブジェクトにする

GKE backend 側に SDK 非依存の不変な toleration 値オブジェクトを置き、`GoogleCloudSettings` はその tuple を保持する。リリースモデルからアプリケーション設定を作る composition root で明示変換し、backend の内部 Job 設定にも tuple のままコピーする。これにより、backend 構築後の呼び出し側による list／dict の変更が Job manifest を変えることを防ぐ。

文字列辞書の list を層間で共有する案はコード量が少ないが、可変性とフィールド名の誤りを各層で再検査する必要があるため採用しない。

### manifestでは設定時だけtolerationsを追加する

tolerations が 1 個以上ある場合だけ `spec.template.spec.tolerations` を作成し、各値オブジェクトを `key`、`operator`、`value`、`effect` を持つ辞書へ順序どおり変換する。未設定時は空配列も追加せず、既存 manifest の構造を維持する。`Exists` の空 `value` も設定どおり明示的に出力し、Pod YAML で 4 フィールドを確認可能にする。

Kubernetes client に `V1Toleration` を渡す案ではなく既存の辞書 manifest を拡張する。現在の backend が SDK 非依存辞書で単体テストされており、この境界を維持できるためである。

### tolerationsはTerraform変数に追加しない

tolerations は Terraform が管理する namespace、KSA、IAM、cluster 接続の属性ではなく、run ごとに backend が作成する Job の属性である。リリース設定から frontend の `GoogleCloudSettings` へだけ伝播させ、Terraform の変数や state には追加しない。

### Autopilot検証にはExistsを使用する

リリース例へ一時的に次の設定を有効化して配備し、Job が受理された場合は run 由来の Pod を `kubectl get pod -o yaml` で取得して 4 フィールドを確認する。

```yaml
tolerations:
  - key: dedicated
    operator: Exists
    value: ""
    effect: NoSchedule
```

Autopilot が Job を拒否した場合は、拒否内容を live test の失敗として記録し、Pod YAML の確認は本番相当環境へ持ち越す。検証終了後は `release.gke.yaml` の設定例をコメントアウトし、検証専用 toleration が既定で有効にならない状態をコミットする。

## Risks / Trade-offs

- [tolerationだけでは対象NodePoolを選択できない] → README または設定例に taint 構成との関係を明記し、node affinity／selector は別変更として扱う。
- [Kubernetesの将来のoperatorまたはeffect追加を設定モデルが拒否する] → 現行 API の値を明示的に許可し、新しい値が必要になった時点でモデルとテストを更新する。
- [リリースモデルとbackendで検証規則が重複する] → 同じ有効・無効ケースをテストし、外部入力の早期エラーと backend の防御を優先する。
- [Autopilotが任意taint向けtolerationを拒否してPodを作成しない] → API エラーを保持してテスト失敗として報告し、本番相当の Standard cluster で YAML と配置を再検証する。

## Migration Plan

1. toleration の設定モデルと内部値オブジェクトを追加し、未設定の既存リリースが同じ値へ解決されることを確認する。
2. リリース設定から `GoogleCloudSettings`、GKE backend、Job manifest まで値を伝播し、単体・統合境界テストを実行する。
3. `release.gke.yaml` の `Exists` 設定を一時的に有効化し、Autopilot へ Job を作成する。受理された場合は Pod YAML、拒否された場合は API エラーを記録する。
4. 検証後に tolerations block をコメントアウトし、全自動テストと静的検査を再実行する。

rollback は tolerations 設定を削除またはコメントアウトして frontend を再起動する。既存 Job、NodePool、taint、Terraform 管理リソースの移行は不要である。
