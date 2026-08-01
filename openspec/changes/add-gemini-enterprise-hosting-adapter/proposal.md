## Why

Claude Agent SDK で作成した任意のエージェントを Gemini Enterprise Agent Platform の実行契約に適合させ、ステートレスなホスティング環境でもセッションと作業ファイルを継続利用できる共通基盤が必要である。本リポジトリは個別エージェントの作り込みではなく、再利用可能なホスティングアダプター、デプロイ手段、利用例を提供する。

## What Changes

- ユーザーが構築した Claude Agent SDK エージェントを登録すると、Gemini Enterprise Agent Platform から呼び出せる API サーバーになる `cas_hosting_adapter` パッケージを追加する。
- Agent Platform Sessions を Claude Agent SDK の Session Store ミラーとして利用し、ユーザー ID、セッション ID、run ID に基づいて会話、主要な進捗、実行状態を復元・追記する。
- Claude Agent SDK が生成する transcript をセッション単位で Google Cloud Storage にも保存し、ステートレスな別インスタンスから確実に再開できるようにする。Session Store ミラーと transcript の内容を比較し、再現可能な情報と乖離を後から検証できるようにする。
- Agent Platform Sessions のセッション一覧 API を調査し、利用可能な SDK 契約があれば、ユーザーが過去のセッションを列挙・再訪するためのアダプター機能として活用する。
- セッション単位のワークスペースを Google Cloud Storage から復元し、未作成時の初期化フックを実行し、エージェント実行後に保存する仕組みを追加する。
- 30分程度の長時間処理をクライアント接続から切り離す非同期実行を追加し、セッション、run、Long-running Operation、Claude セッション、ワークスペースを別々の識別子で追跡する。
- 1セッションにつき active run を最大1件とし、実行中のrunがある場合は明示的にキャンセルして停止を確認するまで次のrunを開始しない。切断後も実行を継続し、状態・主要イベント・最終結果を再取得できるようにする。
- API リクエストの受付からセッションおよびワークスペースの復元、エージェント実行、永続化、キャンセル、後処理までのライフサイクルを統合する。
- メッセージ長、実行時間、idle timeout、ワークスペース容量、復元可能期間を安全な既定値で制限し、必要な項目を API インスタンス設定で変更可能にする。
- プロジェクト、リージョン、コンテナイメージ、リソース名、サービスアカウント、ストレージ、実行制限をバージョン付き YAML リリース設定として一元管理し、デプロイスクリプトへ `--config` 引数で渡せるようにする。クラウドリソースを変更する前に設定を検証し、未知の項目、不正な組み合わせ、YAML へのシークレット埋め込みを拒否する。
- Agent Platform へのデプロイスクリプトと、必要な Google Cloud リソースおよび IAM を作成する Terraform 構成を追加する。外向き通信は、Agent Gateway を関連付けず通常の通信を許可する `unrestricted` と、Agent-to-Anywhere Gateway、Agent Registry、IAM を用いて指定ホスト以外を拒否する `allowlist` から選択可能にする。
- `allowlist` では完全一致のホスト名を管理し、アダプターの動作に必要な Google Cloud、Vertex AI、Claude on Vertex 等の宛先を有効な許可リストへ補完する。リリース前に実際に適用される宛先と Gateway の適用モードを表示し、意図しない通信遮断を防止する。
- Docker イメージ、Claude Agent SDK エージェント、Streamlit フロントエンドの実行可能なサンプルを追加する。
- 単体テストと外部サービスを差し替えた統合テストにより、テナント分離、永続化、失敗時の動作、API 契約を検証する。

## Capabilities

### New Capabilities

- `agent-platform-api`: 登録された Claude Agent SDK エージェントを Agent Platform の通常・ストリーミング・長時間非同期実行の契約で呼び出し、状態確認とキャンセルを行う機能。
- `agent-session-persistence`: Agent Platform Sessions に会話・主要イベント・run状態をミラーし、Claude transcript を GCS に保存・復元・比較するとともに、利用可能ならセッション一覧を提供する機能。
- `workspace-persistence`: セッション別ワークスペースと Claude transcript を Google Cloud Storage と実行環境の間で安全に復元・初期化・保存・期限削除する機能。
- `invocation-lifecycle`: session、run、Long-running Operation、Claude session、workspace を関連付け、単一active run、キャンセル、切断後の継続、永続化を一貫してオーケストレーションする機能。
- `agent-deployment`: バージョン付き YAML リリース設定を検証してコンテナ化したアダプターを Agent Platform へ配備し、必要なクラウド基盤と、任意の Agent Gateway による外向き通信制御を Terraform で構築する機能。
- `hosting-samples`: Docker、サンプルエージェント、Streamlit クライアントを使って利用方法を確認できる機能。

### Modified Capabilities

なし。

## Impact

- 新規コード: `cas_hosting_adapter/`、`example/`、`sample_frontend/streamlit/`、デプロイスクリプト、`terraform/`、テスト。
- 公開 API: エージェント登録・アプリ生成 API、Agent Platform の reasoning engine HTTP エンドポイント、非同期runの開始・状態確認・キャンセル、Session Store および Workspace Store の抽象化、YAML リリース設定スキーマとデプロイスクリプトの `--config` 引数。
- 主な依存先: Claude Agent SDK、FastAPI/ASGI、Google Cloud Vertex AI Agent Platform、Google Cloud Storage、Google Cloud Agent Gateway、Google Cloud Agent Registry、Streamlit、Terraform Google Provider。
- 外部リソース: Agent Engine と Long-running Operations、Agent Platform Sessions、ワークスペース・Claude transcript・非同期入出力用 GCS バケット、Artifact Registry、実行サービスアカウントと最小権限 IAM、および `allowlist` 選択時の Agent-to-Anywhere Gateway、Agent Registry 宛先登録、関連 IAM ポリシー。
