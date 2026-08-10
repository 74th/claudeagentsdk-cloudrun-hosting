## ADDED Requirements

### Requirement: SDK処理の推定費用と処理時間を終端イベントへ保存する
システムはClaude Agent SDKがrunのResultMessageで返した推定総費用USDとSDK処理時間を、そのrunに属する成功またはエラーの終端イベントへ任意の数値項目として保存しなければならない（SHALL）。推定総費用にはSDKが算定したWeb Search利用費を含む総額をそのまま使用し、システム独自の検索単価を加算してはならない（MUST NOT）。SDKが項目を返さない場合または有効な非負数として扱えない場合、0に置き換えて保存してはならない（MUST NOT）。

#### Scenario: 正常終了時に両方の値が返る
- **WHEN** SDKが正常終了のResultMessageで推定総費用と処理時間を返す
- **THEN** システムは最終出力と同じrunの終端イベントへ両方の値を保存する

#### Scenario: エラー終了までに費用が発生する
- **WHEN** SDKがエラーのResultMessageで推定総費用または処理時間を返す
- **THEN** システムは取得できた値をエラー終端イベントへ保存し、失敗したrunの消費を失わない

#### Scenario: SDKが値を返さない
- **WHEN** ResultMessageで推定総費用または処理時間が欠けているか無効な値である
- **THEN** システムは該当項目を省略し、未取得の値を0として記録しない

#### Scenario: Web Searchを利用する
- **WHEN** runがWeb Searchを利用し、その費用を含む推定総費用をSDKが返す
- **THEN** システムはその推定総費用を重複加算せず保存する
