## Context

現在の `sample_frontend/app.py` は Streamlit の描画に加え、`ControlClient` の構築、セッション/run 操作、イベント履歴の正規化、購読制御を保持している。一方、`example/agent.py` は Cloud Run Job のエントリーポイントであり、サンプルの配置規則も統一されていない。既存の `ControlClient` と永続モデルを変更せず、CLI、Streamlit、Slack の共通境界を設ける必要がある。

Slack はリクエストへの短時間応答、API rate limit、重複イベント配送を前提とする。run は長時間実行されるため、Slack イベントハンドラーで完了を待たず、バックグラウンド処理でストリームを消費する。また、Slack スレッドと内部セッションの対応はプロセス寿命を越えて保持する必要がある。

## Goals / Non-Goals

**Goals:**

- `ControlClient` のセッション/run API を、CLI と複数 UI が利用できる一つのアプリケーションサービスにまとめる。
- 保存済みイベントとリアルタイム購読の境界で欠落や重複のない反復可能なストリームを提供する。
- フロントエンド固有処理を入力変換と表示処理へ限定する。
- Slack の再送、プロセス再起動、rate limit を考慮したサンプル境界を示す。
- 既存機能を用途別の `example` パッケージへ段階的に移行する。

**Non-Goals:**

- `ControlClient`、Job 実行バックエンド、Firestore の既存 run/event スキーマを全面的に再設計すること。
- Slack App の自動作成や Terraform による Slack workspace 設定を行うこと。
- Streamlit と Slack で完全に同一の見た目やツール表示を提供すること。
- 複数 Bot プロセス間の厳密なリーダー選出を構築すること。

## Decisions

### 共通層をアプリケーションサービスとイベントストリームに分ける

`example` のフロントエンド非依存パッケージに、設定から `ControlClient` を生成する処理、会話開始処理、履歴整形、run イベントストリームを置く。開始結果は session/run の識別子を返し、ストリームはフレームワークに依存しないイベント値を反復的に返す。Streamlit の state、Slack の payload、標準入出力を共通層へ持ち込まない。

`ControlClient` を各 UI が直接呼ぶ案はコード量が少ないが、履歴と購読の接続、終端判定、重複排除が再び分岐するため採用しない。既存 `ControlClient` 自体へ UI 向け表現を追加する案も、配備ライブラリとサンプル表示上の都合を結合するため採用しない。

### カーソルとイベントIDで保存済み取得から購読へ接続する

ストリーム開始時に保存済みイベントを読み、最後のカーソルから購読を開始する。接続境界で再取得されたイベントはイベント ID の有限集合で除外する。購読が一時中断した場合も最後に配信したカーソルから再接続する。終端イベントを配信した後、最新 run 状態を確認してストリームを閉じる。

購読だけに依存する案は接続前イベントを欠落させ、定期 polling だけの案は遅延と読み取り回数が増えるため採用しない。

### CLIは一イベント一レコードで即時flushする

CLI は release config、user ID、prompt、任意の session ID と idempotency key を引数で受ける。開始結果とイベントを一イベント一 JSON レコードとして標準出力へ書き、各レコードで flush する。人間向け表示よりも、ストリーミングの自動テストと `jq` 等での観察を優先する。診断は標準エラー、run の失敗・キャンセルと設定不備は非ゼロ終了とする。

整形済みテキストだけを出す案はイベント種別や境界の検証が難しいため採用しない。

### SlackはSocket Modeで受信し、応答更新を間引く

サンプルは公開 HTTP endpoint を要求しない Socket Mode を使用する。イベントを受領後すぐ acknowledge し、run 開始とストリーム消費は管理されたバックグラウンドタスクで行う。最初にスレッド返信を作り、本文イベントを集約して一定間隔または一定文字数ごとに更新する。ツール進捗と終端状態は簡潔な表示へ変換し、Slack API の rate limit 応答では指定時間後に再試行する。

Events API の HTTP endpoint は本番配備に適するが、ローカルサンプルで公開 URL と署名検証設定が必要になるため初期実装では採用しない。イベントごとの新規メッセージ投稿もスレッドを過剰に流し rate limit を受けやすいため採用しない。

### Slackスレッド対応は専用ポートとFirestore実装で永続化する

対応キーは Slack team ID、channel ID、親 thread timestamp とし、値にアプリケーション user ID と session ID を保存する。Bot の表示層は `SlackThreadSessionStore` 相当の小さなポートだけに依存し、サンプル実装は既存 release config が指す Firestore database の専用 collection を利用する。初回メッセージではセッション開始成功後に条件付き作成し、重複配送時は Slack event ID を冪等性キーへ変換する。

メモリ内辞書は再起動で会話を失うため採用しない。Slack メッセージ本文やタイトルへ session ID を埋め込む案は利用者に内部識別子を露出し、編集・削除にも弱いため採用しない。

### サンプルを移動し、互換shimは設けない

`example/agent.py` は `example/agent/`、`sample_frontend/` は `example/streamlit_frontend/` へ移し、新たに共通チャットパッケージ、CLI、`example/slackbot_frontend/` を配置する。`pyproject.toml` の package discovery、テスト、Dockerfile、README の参照を同時に更新する。サンプルコードであり公開ライブラリ API ではないため、旧 import path の shim は設けず、移行を明示する。

## Risks / Trade-offs

- [Slack API の rate limit により表示が遅延する] → 応答本文を集約して更新頻度を制限し、`Retry-After` に従う。
- [Slack イベントの再送で run が重複する] → event ID 由来の冪等性キーとスレッド対応の条件付き作成を併用する。
- [保存済み取得と購読の境界でイベントが重複または欠落する] → カーソル再開とイベント ID の重複排除をテストし、購読再接続も同じ経路へ統一する。
- [バックグラウンドタスク中に Bot が終了する] → session/run とスレッド対応を先に永続化し、再起動後の後続入力で状態を復元可能にする。進行中表示の自動再開は初期範囲外とする。
- [Slack SDK により基本インストールが重くなる] → Slack と Streamlit は個別 dependency group に分け、コア利用者へ不要な依存を導入しない。
- [旧サンプルパス利用者が起動できなくなる] → README に旧パスから新パスへの対応表を示し、全リポジトリ内参照を同じ変更で更新する。

## Migration Plan

1. 共通チャットサービスと単体テストを追加し、既存 `ControlClient` を使う CLI でストリームを検証する。
2. ジョブ用エージェントと Streamlit を新しい `example` 配下へ移し、Streamlit を共通サービス利用へ切り替える。
3. Slack 用依存 group、スレッド対応ストア、Socket Mode Bot とテストを追加する。
4. package discovery、Dockerfile、起動コマンド、README を新構成へ更新し、旧ディレクトリを削除する。
5. 単体・統合テストと静的検査を実行し、CLI、Streamlit、Slack の手動確認手順を記録する。

問題が発生した場合は変更単位で旧サンプル配置へ戻せる。永続 run/event スキーマは変更しないため、既存データのロールバック作業は不要である。Slack スレッド対応 collection は追加データであり、旧版から参照されない。
