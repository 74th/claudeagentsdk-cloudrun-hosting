## ADDED Requirements

### Requirement: GKE Jobへtolerationsを設定する
GKE バックエンドは、リリース設定で指定された 0 個以上の toleration を、各 GKE Job の Pod テンプレートへ適用しなければならない（SHALL）。各 toleration は `key`、`operator`、`value`、`effect` を保持し、`operator` は `Equal` または `Exists`、`effect` は `NoSchedule`、`PreferNoSchedule`、`NoExecute` のいずれかでなければならない（MUST）。`operator: Exists` の `value` は空でなければならず（MUST）、不正な設定は Kubernetes API を呼び出す前に拒否しなければならない（SHALL）。

#### Scenario: 複数のtolerationをPodテンプレートへ反映する
- **WHEN** GKE リリース設定に有効な toleration が複数指定され、run の開始が要求される
- **THEN** 作成される Job の `spec.template.spec.tolerations` は、設定された順序と各要素の `key`、`operator`、`value`、`effect` を保持する

#### Scenario: tolerationsを指定せず既存動作を維持する
- **WHEN** GKE リリース設定に tolerations が指定されていない状態で run の開始が要求される
- **THEN** バックエンドは tolerations によるスケジューリング制約を追加せず、従来と同じ Job を作成する

#### Scenario: Exists operatorを反映したPodを確認する
- **WHEN** 空の `value` と `operator: Exists` を持つ toleration で Job がクラスタに受理され Pod が作成される
- **THEN** Kubernetes API から取得した Pod の YAML に、指定した `key`、`operator`、空の `value`、`effect` が反映されている

#### Scenario: 不正なtolerationを拒否する
- **WHEN** 未対応の operator または effect、もしくは空でない `value` を持つ `operator: Exists` が設定される
- **THEN** システムはリリース設定を構成エラーとして拒否し、GKE Job を作成しない
