## Purpose

ホスティングアダプターを再現可能な設定でコンテナ化して Gemini Enterprise Agent Platform へ配備し、長時間非同期実行と永続化に必要なGoogle Cloudリソースと権限をコードで構築できるようにする。

## ADDED Requirements

### Requirement: 必要なクラウド基盤を Terraform で構築する
Terraform 構成は、必要な Google Cloud API、コンテナレジストリ、非同期入出力・workspace・Claude transcript用 GCS バケット、実行サービスアカウント、および実行に必要な IAM 権限を作成しなければならない（SHALL）。外向き通信モードが `allowlist` の場合に限り、Agent-to-Anywhere Gateway、Agent Registry の宛先登録、および関連 IAM ポリシーも作成しなければならない（SHALL）。

#### Scenario: 新規プロジェクトへの適用
- **WHEN** 利用者が必須変数を設定して Terraform を適用する
- **THEN** Agent Platformへの非同期配備とSession、Operation、GCS利用に必要な基盤が作成され、デプロイに必要な値がoutputされる

#### Scenario: allowlist用基盤を計画する
- **WHEN** 外向き通信モードを `allowlist` に設定して Terraform plan を実行する
- **THEN** Agent-to-Anywhere Gateway、許可ホストの Agent Registry 登録、およびランタイムから登録済み宛先へのアクセスに必要な IAM ポリシーが計画される

#### Scenario: unrestricted用基盤を計画する
- **WHEN** 外向き通信モードを `unrestricted` に設定して Terraform plan を実行する
- **THEN** Agent Gateway、Agent Registry の宛先登録、および Gateway 関連 IAM ポリシーは計画されない

### Requirement: GCSの保持期限を構成する
Terraform構成はworkspace、Claude transcript、非同期入出力を保存する対象GCSオブジェクトへ、既定で作成から7日後に削除するlifecycle ruleを設定しなければならない（SHALL）。保持日数はTerraform変数で変更可能にしなければならない（SHALL）。

#### Scenario: 既定の保持期限で適用する
- **WHEN** 利用者が保持日数を指定せずTerraformを適用する
- **THEN** 対象GCSバケットには作成から7日後にオブジェクトを削除するlifecycle ruleが設定される

### Requirement: 実行主体へ最小権限を付与する
Terraform 構成は、ランタイムサービスアカウントに Agent Platform Sessions、Long-running Operations、指定GCSバケットを利用するための権限のみを付与し、プロジェクト全体のストレージ管理者権限を付与してはならない（MUST NOT）。

#### Scenario: ランタイム IAM の確認
- **WHEN** Terraform plan の IAM リソースを検査する
- **THEN** ストレージ権限は対象バケットへスコープされ、ランタイムにオーナーまたは編集者ロールが付与されていない

### Requirement: バージョン付きYAMLでリリース設定を管理する
デプロイスクリプトは `--config <path>` でバージョン付き YAML リリース設定を受け取らなければならない（SHALL）。設定スキーマは少なくとも `schema_version`、リリース名、Google Cloudプロジェクト、リージョン、Agentの表示名、コンテナイメージURI、ランタイムサービスアカウント、GCS設定、実行時設定、および外向き通信設定を表現できなければならない（SHALL）。設定スキーマは秘密値を受け取るフィールドを定義してはならない（MUST NOT）。

#### Scenario: YAML設定を読み込む
- **WHEN** 利用者がサポート対象の `schema_version` と必須項目を含む YAML を `--config` で指定する
- **THEN** スクリプトは設定を読み込み、同じ入力から対象プロジェクト、リージョン、Agent、ストレージ、実行制限、および外向き通信方式を決定する

#### Scenario: 未対応の設定を拒否する
- **WHEN** YAML に未対応の `schema_version`、未知のキー、必須項目の欠落、型不一致、または不正な値が含まれる
- **THEN** スクリプトはクラウドリソースを変更する前に失敗し、対象フィールドと理由を表示する

#### Scenario: 秘密情報用の項目を拒否する
- **WHEN** 利用者が認証鍵、アクセストークン、または秘密値を未知の設定キーとして YAML に追加する
- **THEN** スクリプトは未知のキーとして設定を拒否し、実行時認証を使用するよう案内する

### Requirement: 外向き通信モードを選択する
YAML リリース設定は `egress.mode` として `unrestricted` または `allowlist` を選択できなければならず（SHALL）、未指定時は `unrestricted` を使用しなければならない（SHALL）。`unrestricted` では Agent Gateway を Agent に関連付けてはならない（MUST NOT）。`allowlist` では指定された Agent-to-Anywhere Gateway を Agent と同一プロジェクトかつ同一リージョンに構築して関連付けなければならない（SHALL）。

#### Scenario: 既定の外向き通信を使用する
- **WHEN** `egress.mode` を省略するか `unrestricted` を指定してデプロイする
- **THEN** Agent Gateway は関連付けられず、Agent は実行環境で通常許可される任意の外部ホストへアクセスできる

#### Scenario: allowlistモードを使用する
- **WHEN** `egress.mode` に `allowlist` を指定し、有効な Gateway 設定と許可ホストを指定してデプロイする
- **THEN** Agent は Agent-to-Anywhere Gateway に関連付けられ、Gateway を経由して登録済みかつ認可済みの宛先へアクセスする

#### Scenario: モードと設定の不整合を拒否する
- **WHEN** `allowlist` で Gateway 名または許可ホストがない、あるいは `unrestricted` で Gateway 固有設定または許可ホストを指定する
- **THEN** スクリプトはクラウドリソースを変更する前に不整合なフィールドを示して失敗する

### Requirement: 許可ホスト以外への通信を制限する
`allowlist` の許可先はスキーム、ポート、パスを含まない完全一致の DNS ホスト名として指定しなければならず（SHALL）、ワイルドカードを許可してはならない（MUST NOT）。デプロイ処理は各許可ホストを Agent Registry の外部宛先として登録し、`ENFORCE` 時には登録および認可されていないホストへの通信を拒否しなければならない（SHALL）。Gateway の適用モードとして `DRY_RUN` または `ENFORCE` を明示的に選択可能にしなければならない（SHALL）。

#### Scenario: 完全一致ホストを許可する
- **WHEN** `allowed_hosts` に `api.example.com` を指定して `ENFORCE` でデプロイする
- **THEN** `api.example.com` は Agent Registry に登録および認可され、別名や別のサブドメインは個別に指定しない限り許可されない

#### Scenario: 指定外ホストを拒否する
- **WHEN** `ENFORCE` の Agent が有効な許可リストにないホストへ接続を試みる
- **THEN** Agent Gateway は接続を拒否し、拒否を観測可能なログへ記録する

#### Scenario: 不正なホスト表現を拒否する
- **WHEN** 許可ホストに URL、パス、ポート、IPアドレス、またはワイルドカードを指定する
- **THEN** スクリプトはクラウドリソースを変更する前に該当値を示して失敗する

#### Scenario: DRY_RUNで段階的に確認する
- **WHEN** Gateway の適用モードを `DRY_RUN` にしてデプロイする
- **THEN** ポリシー違反候補はログへ記録されるが通信は遮断されず、利用者は `ENFORCE` へ切り替える前に必要な宛先を確認できる

### Requirement: 実効許可リストを決定して事前表示する
デプロイ処理は `allowlist` の利用者指定ホストに、選択された機能が必要とする Google Cloud Storage、Vertex AI、Claude on Vertex、および Agent Platform の完全一致ホストを補完しなければならない（SHALL）。クラウドリソースを変更する前に、正規化したリリース設定、補完元を区別できる実効許可ホスト一覧、および Gateway の適用モードを表示しなければならない（SHALL）。

#### Scenario: 必須ホストを補完する
- **WHEN** `allowlist` の利用者指定ホストに、構成済み機能が必要とする Google Cloud の宛先が含まれていない
- **THEN** デプロイ処理は必要な完全一致ホストを実効許可リストへ追加し、利用者指定か自動補完かを区別して表示する

#### Scenario: 実効設定をデプロイ前に確認する
- **WHEN** 有効な YAML を使ってデプロイを開始する
- **THEN** スクリプトは変更対象のプロジェクト、リージョン、Agent名、コンテナイメージ、外向き通信モード、Gateway適用モード、および実効許可ホストをクラウド変更前に表示する

### Requirement: Agent Platform へコンテナをデプロイする
デプロイスクリプトは、YAML リリース設定からプロジェクト、リージョン、イメージ URI、表示名、サービスアカウント、GCS設定、および外向き通信設定を取得し、通常・ストリーミング・長時間非同期実行に必要な操作を宣言した Agent Engine を作成または更新しなければならない（SHALL）。

#### Scenario: デプロイに成功する
- **WHEN** 有効な入力と認証情報でデプロイスクリプトを実行する
- **THEN** スクリプトは Agent Engine の作成または更新完了を待ち、通常・ストリーミング・非同期呼び出しに使用できるリソース名を出力する

#### Scenario: 必須入力がない
- **WHEN** 必須のデプロイ設定が不足している
- **THEN** スクリプトはクラウドリソースを変更する前に失敗し、不足項目を表示する

### Requirement: 実行時設定をデプロイへ渡す
デプロイスクリプトは最大実行時間、idle timeout、セッション復元可能期間、workspace容量上限、ログレベルをランタイム設定として渡せなければならない（SHALL）。未指定時は最大実行時間30分、idle timeout 30分、復元可能期間1日、圧縮前後のworkspace上限100 MBを使用しなければならない（SHALL）。

#### Scenario: 既定設定でデプロイする
- **WHEN** 利用者が実行制限を上書きせずにデプロイする
- **THEN** Agent Engineは既定の時間・保持・容量制限で起動する

### Requirement: 秘密情報を成果物へ埋め込まない
コンテナ、Terraform 構成、デプロイスクリプトは認証鍵やアクセストークンをソース、イメージ、Terraform の出力へ埋め込んではならず（MUST NOT）、Google Cloud の実行時認証を利用しなければならない（SHALL）。本フレームワークはSecret Managerリソースおよび秘密値の管理を行ってはならない（MUST NOT）。

#### Scenario: 配布物を検査する
- **WHEN** リポジトリとビルドコンテキストを検査する
- **THEN** サービスアカウント秘密鍵やアクセストークンが含まれず、ランタイムは割り当てられたサービスアカウントで認証する
