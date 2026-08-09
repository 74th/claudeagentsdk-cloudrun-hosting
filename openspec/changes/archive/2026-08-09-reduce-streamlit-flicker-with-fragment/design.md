## Context

`example/streamlit_frontend/app.py` は active Run の表示末尾で 2 秒待機してから無条件の `st.rerun()` を呼ぶ。これによりイベントと状態は追従できるが、トップレベルの `render` がサイドバー、セッション取得、見出し、会話履歴、入力をすべて再実行する。Streamlit 1.59.2 は fragment 単位の再実行をサポートしており、永続データや共通チャットサービスを変更せず UI の再実行境界を狭められる。

既存の質問ウィジェットは回答待ちになると自動更新を止める必要がある。また、Run 終了時にはセッションの更新日時や Agent が決定したセッション名が変わり得るため、サイドバーは最終的に全体再実行で同期する必要がある。

## Goals / Non-Goals

**Goals:**

- 定期ポーリングによる再実行を会話・Run の動的領域に閉じ込める。
- Run 終端を検出した時点で一度だけアプリ全体を再実行し、fragment 外も同期する。
- 質問回答ウィジェット、手動更新、キャンセルという既存操作を維持する。
- 更新判断を UI から切り離してテスト可能にする。

**Non-Goals:**

- Firestore watch や共通チャットクライアントの購読方式を変更すること。
- サイドバー自体を定期更新すること。
- Slack Bot の更新方式、Run／Event のデータモデル、更新間隔を変更すること。
- 会話イベントをイベント単位の個別 DOM 差分へ分解すること。

## Decisions

### 1. ページ外枠と動的 Run 領域を分離する

トップレベルの `render` はタイトル、identity、設定、セッション一覧、draft／選択状態、および chat input を扱う。選択済みセッションの履歴、Run 状態、質問、タスク、更新・キャンセル操作は fragment 化した描画関数へ移す。fragment は毎回、選択中の session ID から最新 Session、Run、Event を取得するため、古いモデルをクロージャに保持しない。選択状態とdraft状態が未初期化の初回表示では、`New session` 操作と同じdraft状態を設定する。

サイドバーだけを fragment にする案は、ちらつきの原因である全体 rerun を残すため採用しない。イベントごとに複数 fragment へ分割する案も、表示順と質問・タスクの合成が複雑になり、今回必要な安定性に対して過剰なため採用しない。

### 2. 条件付きの fragment スコープ再実行を継続する

動的領域を `run_every=2` の fragment として登録し、active Run が継続中かつ回答待ちでない間だけ fragment が起床する。これにより既存の 2 秒周期と質問待ち停止を保ちつつ、トップレベルを再実行しない。手動の「今すぐ更新」も fragment スコープとする。

pending 質問が検出された場合は Session State のフラグを更新してアプリスコープへ一度戻り、次の全体実行では `run_every=None` でfragmentを再登録する。回答送信後にフラグを戻して定期更新を再開する。

### 3. 終端遷移ではアプリスコープへ昇格する

fragment は更新開始時に Firestore の Session、Run、最新イベントを読み、再取得した Session で active Run が解除されたか、対象 Run が terminal になったか、finish イベントが現れたかを判定する。Cloud Run の状態 API は表示の必須経路にしない。状態 API の応答が遅い場合でも、worker が Firestore に保存したイベントを先に表示できる必要があるためである。終端を検出した場合はアプリスコープの rerun を一度要求する。finishイベントの保存とSessionのactive Run解除は別の永続化操作のため、finishだけを先に観測した場合は定期fragmentを止めず、active Runが解除された遷移も監視する。全体再実行後は Session に active Run がないため同じ条件が再成立せず、再実行ループにならない。

finish イベントだけを条件にする案は、異常終了やイベント保存より先に状態が終端化した場合にサイドバー同期が遅れる。Run 状態だけを条件にする案は、最終イベントが先に観測できる場合の同期を遅らせる。このため両方を終端シグナルとして扱う。

### 4. 更新遷移を小さな判定関数として検証する

「部分更新を継続」「質問待ちで停止」「全体同期」の判断を、active Run、最新 Run／Session、および interaction／event の状態を入力にした副作用のない判定へ寄せる。テストでは Streamlit ランタイムそのものを起動せず、この遷移と fragment に渡す scope を検証する。既存の ViewModel テストは reconcile の一時障害時に active Run を維持する契約を引き続き担保する。

### 5. 定期更新では revision を先に確認する

fragment の定期起動自体は維持するが、毎回の起動で履歴全体や interaction を取得しない。Session の更新日時・active Run、Run の状態・終端情報、および Run 配下の最新イベント ID／sequence から小さな revision を作り、前回の revision と比較する。revision が同じ場合は前回の表示モデルを Session State から再利用し、revision が変わった場合だけ履歴と interaction を再取得して動的領域を再構築する。

最新イベントは Firestore では sequence 降順の limit 1 クエリ、インメモリストアでは末尾要素から取得する。Run 終端や質問回答も Run／イベントの revision に含まれるため、既存の終端同期と質問待ち停止を維持できる。

### 6. ユーザーメッセージを fragment 外に固定する

トップレベルの `render` は選択セッションの履歴から user イベントだけを描画し、動的 fragment に渡す履歴から user イベントを除外する。新規プロンプト送信時は `view.start` の前にも入力値を user chat message として描画するため、Run の予約・dispatch が完了するまで空白にならない。agent、tool、progress、question、task、および Run 操作は引き続き fragment 内で描画する。

## Risks / Trade-offs

- [Risk] fragment 再実行時に会話の動的領域全体は再描画されるため、その領域内の微小な変化は残る → サイドバーとページ外枠を境界外に置き、利用者が問題としている全画面のちらつきを抑える。必要なら将来イベント領域をさらに分割する。
- [Risk] finish イベントと Run 状態の反映順に差がある → いずれかを終端シグナルとし、全体再実行後に永続状態を再取得する。
- [Risk] 一時エラーを終端と誤認すると全体更新が増える → `ExecutionTemporaryError` は既存どおり握って active 状態を維持し、次の fragment 更新へ進む。
- [Trade-off] 明示的な待機は fragment 実行を更新間隔だけ保持する → 現行方式と同じ負荷特性を維持しつつ scope を限定する。将来、質問待ち停止を安全に表現できる場合は `run_every` へ置換できる。

## Migration Plan

1. 動的領域の描画と更新判断を抽出し、既存の全アプリ `st.rerun()` を fragment スコープへ置き換える。
2. 終端時のみアプリスコープへ昇格する処理と回帰テストを追加する。
3. Streamlit フロントエンドの単体テストを実行し、ローカル UI で実行中のサイドバー安定性、質問入力、正常終了、異常終了、キャンセルを確認する。

ロールバックは fragment 化した描画関数をトップレベルへ戻し、従来の全アプリ更新へ戻す。永続データや API の移行はない。
