# Firestore event payload と batching の既定値

Firestore Standard の document 上限は 1 MiB、API request 上限は 10 MiB、transaction は
270 秒（idle 60 秒）である。event 追記は run document と event document の競合を最小化し、
上限から十分に離した次の値を採用する。

| 項目 | 既定値 | 根拠 |
| --- | ---: | --- |
| 正規化済み event payload の最大 UTF-8 bytes | 32 KiB | 1 MiB document 上限の 3% 未満に抑え、metadata・index・将来 field の余地を残す。大きい tool 入出力は object store reference にする。 |
| 1 event document の最大概算 bytes | 64 KiB | event ID、type、時刻、sequence、schema version と公開 error を含めても document 上限から大きく離す。 |
| 1 回の event batch の最大 event 数 | 20 | listener への可視化遅延を短く保ち、run sequence 更新の contention を抑える。 |
| 1 回の event batch の最大概算 bytes | 256 KiB | 10 MiB request 上限の 2.5% に抑え、SDK encoding と retry の余地を確保する。 |
| 最大 batch 待機時間 | 250 ms | token delta を無制限に書かず、進捗をほぼ即時に購読者へ見せる。 |
| transaction callback の業務処理時間 | 5 秒未満 | 60 秒 idle / 270 秒総時間より十分短くし、read を全て write より前に行う。 |

## 実装上の制約

- `payload` の UTF-8 byte 長が 32 KiB を超えたら、内容を Firestore へ切り詰めて保存せず、
  object store の参照・hash・安全な要約へ変換する。変換不能なら公開 validation error とする。
- batch は event ID ごとの冪等追記である。送信失敗時は同じ event ID を再送する。
- run の `next_sequence` と event 作成は transaction に含めるが、長文 payload の整形、
  archive、SDK 実行、listener callback を transaction 内で行わない。
- `ABORTED` / `UNAVAILABLE` など一時障害は制限付き backoff の対象にする。
  `FAILED_PRECONDITION`（index 不足など）、入力不正、権限、quota は無条件に再試行しない。

この値は Firestore Native Standard の公式 quota と task 1.4 の live probe を根拠にする。
実運用で payload 分布または contention を観測した場合だけ、version 付きリリース設定で変更する。

