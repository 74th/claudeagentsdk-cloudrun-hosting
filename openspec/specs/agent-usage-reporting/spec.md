# agent-usage-reporting Specification

## Purpose

エージェントの run ごとの利用実績を構造化し、アプリケーションがログや任意の分析基盤へ安全に記録できる hook 契約を提供する。

## Requirements

### Requirement: run の利用実績を構造化して提供する
システムは、終端状態を確定した run の利用実績として、ユーザー名、run ID、セッション名、Claude Agent SDK の推定総費用 USD、記録時刻、および SDK 処理時間ミリ秒を含む構造化レコードを提供しなければならない（SHALL）。ユーザー名には run に関連付けられた user ID、セッション名には run に関連付けられたセッションの表示名、記録時刻にはタイムゾーン付き UTC の run 終端時刻を使用しなければならない（MUST）。

#### Scenario: SDK が料金と処理時間を返して正常終了する
- **WHEN** Claude Agent SDK が推定総費用と処理時間を返した run の終端状態が確定する
- **THEN** 利用実績はその run のユーザー名、run ID、セッション名、推定総費用、SDK 処理時間、および UTC の記録時刻を保持する

#### Scenario: ユーザー名が email アドレスである
- **WHEN** run に関連付けられた user ID が email アドレスである
- **THEN** 利用実績のユーザー名はその値を欠落または別の識別子へ置換せず保持する

#### Scenario: SDK の利用量項目が欠けている
- **WHEN** Claude Agent SDK が推定総費用または処理時間を返さないか、有効な非負数を返さない
- **THEN** 利用実績は該当項目を未取得として表し、0 に置き換えない

### Requirement: アプリケーション定義 hook へ終端実績を通知する
Agent runtime は、アプリケーションが利用実績を受け取る任意の Python hook を登録できなければならない（SHALL）。hook を登録した場合、システムは所有権を取得した run の終端状態を永続化した後に、その run の利用実績を 1 回通知しなければならない（SHALL）。hook を登録しない場合、既存の run 実行結果を変更してはならない（MUST NOT）。

#### Scenario: hook を登録して run が完了する
- **WHEN** 利用実績 hook を登録した runtime が所有権を取得した run の終端状態を正常に永続化する
- **THEN** runtime は永続化済みの run と同じ run ID を持つ利用実績を hook へ 1 回渡す

#### Scenario: 重複した Job が run の所有権を取得できない
- **WHEN** Job が終端済み run または別の実行が所有する run を開始しようとして処理をスキップする
- **THEN** スキップした Job は利用実績 hook を呼び出さない

#### Scenario: hook を登録しない
- **WHEN** アプリケーションが利用実績 hook を指定せず run を実行する
- **THEN** runtime は従来どおり run を終端状態へ確定し、同じ終了コードを返す

### Requirement: 利用実績 hook の障害を run lifecycle から隔離する
システムは利用実績 hook が送出した例外を記録し、既に永続化した run の終端状態またはプロセス終了コードを変更してはならない（MUST NOT）。hook の再試行や外部送信の冪等性は hook 実装側の責務としなければならない（SHALL）。

#### Scenario: 利用実績の外部送信に失敗する
- **WHEN** run の終端状態を永続化した後で利用実績 hook が例外を送出する
- **THEN** runtime は hook の失敗をログへ記録し、hook 呼び出し前に決定した run の終端状態と終了コードを維持する

### Requirement: サンプル Agent が利用実績をログ出力する
サンプル Agent は、利用実績 hook を Python 関数として定義して runtime へ登録し、受け取ったユーザー名、run ID、セッション名、推定総費用 USD、記録時刻、および SDK 処理時間ミリ秒をログへ出力しなければならない（SHALL）。

#### Scenario: サンプル Agent の run が終端状態になる
- **WHEN** 利用実績 hook を登録したサンプル Agent の run が終端状態へ確定する
- **THEN** サンプル Agent のログに、その run を識別できる全利用実績項目が 1 レコードとして出力される
