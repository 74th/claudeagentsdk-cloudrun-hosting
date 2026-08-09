## 1. 更新境界と判断ロジック

- [x] 1.1 active Run、終端状態／finish イベント、pending 質問から「部分更新継続・自動更新停止・全体同期」を決定する副作用のない判定ロジックを追加する
- [x] 1.2 選択中 session ID から最新の Session、Run、履歴、interaction を取得して描画できるよう、既存の会話・状態・質問・タスク・Run 操作の描画を動的領域へ抽出する

## 2. Streamlit fragmentによる部分更新

- [x] 2.1 動的領域を Streamlit fragment として構成し、処理継続中の定期更新と「今すぐ更新」を fragment スコープの rerun に変更する
- [x] 2.2 サイドバー、設定、セッション一覧、draft／選択状態、chat input を fragment 外に維持し、active Run 中の周期的なアプリ全体 rerun を除去する
- [x] 2.3 pending 質問の表示中は fragment の自動更新を止め、回答送信後に安全に更新を再開する
- [x] 2.4 finish イベント、正常・異常・キャンセルを含む終端状態を検出したときだけアプリスコープで一度 rerun し、サイドバーと最終表示を同期する
- [x] 2.5 状態再照会の一時エラーでは実行中表示と fragment 更新を維持し、終端または全体更新として扱わない

## 3. テストと動作確認

- [x] 3.1 処理継続、finish／終端、pending 質問、一時エラーに対する更新判断の単体テストを `tests/test_sample_frontend.py` に追加する
- [x] 3.2 fragment 内の定期・手動更新が fragment scope、終端同期だけが app scope を要求することを検証する回帰テストを追加する
- [x] 3.3 Streamlit フロントエンドのテスト一式を実行し、既存のセッション選択、質問回答、タスク表示、キャンセルに回帰がないことを確認する
- [x] 3.4 ローカル UI で active Run 中にサイドバーが周期再描画されず、finish 後にセッション名と更新日時が同期されることを確認する

## 4. 変更検知による取得抑制

- [x] 4.1 Session、Run、最新イベントから定期更新用の revision を取得し、前回と同一なら履歴・interaction の再取得を省略する
- [x] 4.2 revision が変化した場合だけ動的表示モデルを更新する単体・回帰テストを `tests/test_sample_frontend.py` に追加する
- [x] 4.3 変更なし／変更ありの定期更新と終端同期を Streamlit AppTest および既存テストで確認する

## 5. ユーザーメッセージの固定表示

- [x] 5.1 user イベントをfragment外で描画し、動的fragmentから除外する
- [x] 5.2 新規・既存セッションのユーザープロンプトがagent応答前から表示され、重複表示されないテストを追加する
- [x] 5.3 入力直後、既存履歴、質問待ち、終端同期のStreamlit UIスモークを実行する
