# 史料室 PC自動収集：承認前の実装案

2026-11-05。これは既存mainから作成したローカル変更です。GitHubへのpush・PR・マージ、タスク登録、実データ収集は未実施です。

## 利用者にとっての動作

- Windowsへログオン中、毎日19:17に収集。停止中の取り逃しは次のログオン時／実行可能時に1回補完します。過去の日数分を繰り返し実行はしません。
- 日本時間19:17を境に予定日を区切り、同じ予定分は二重実行しません。朝のログオンによる前夜分の補完で、今夜19:17の予定分は消費しません。全API失敗・部分失敗もその予定分は実行済みになります。即時再試行は`--force`です。異常終了で完了記録がなければ次回再実行できます。
- PCの電源OFF・休止・スリープ中は収集しません。スリープ解除や24時間起動は設定しません。タスクは本人がWindowsへログオンしているときだけ動きます。
- CiNii Researchと、NDLの`zassaku`・`zassaku-online`・`iss-ndl-opac`だけ収集します。7テーマ・19検索語を維持します。
- 1検索語・1対象につき100件×最大5ページ。0件・最終ページで終了。既知書誌に出会っても途中終了せず重ねて確認します。
- CiNiiは出版年の新しい順。出版年で除外せず、古い文献の新規登録も検索範囲に含めます。更新日時を使った厳密な差分取得ではありません。NDLは既定順の先頭500件までです。
- 上限に達したら「一部失敗・取得上限あり」と記録します。500件超の全件取得・網羅性は保証しません。広い検索語は分割が必要です。初回も日常実行も同じ上限です。
- 19語×4対象で基本76要求。最大380要求＋既存の有限再試行。要求間は最低2秒、各要求15秒タイムアウト。混雑時は長くなります。Windowsタスクは1時間で打ち切る設定です。
- 画面の新着は「最近7日で初めて取得した書誌」であり「最近7日で刊行された論文」ではありません。再取得だけでは新着になりません。自動統合によって件数は変わる場合があります。
- 実行結果・新規追加数・取得上限等・バックアップ日時をローカル画面に表示します。問題が2回以上続いた場合、Windows実行スクリプトが10秒間の案内を表示します。タスクを開かずに完全監視するものではありません。

## 保存場所・公開範囲・費用

| 内容 | 場所・範囲 |
|---|---|
| コード | 既存のGit作業フォルダ。承認後にコードと文書だけ公開GitHubへPR |
| 正本DB | `%LOCALAPPDATA%\Shiryoshitsu\data\catalog.db` |
| 実行履歴・検索語・保存条件の記録 | 同じDB。公開しない |
| ローカルバックアップ | `%LOCALAPPDATA%\Shiryoshitsu\backups` |
| CiNii ID | `%LOCALAPPDATA%\Shiryoshitsu\secrets\cinii.dpapi`。Windows DPAPI暗号化 |
| 追加バックアップ | `-BackupDir`で指定する外付け媒体等。現時点では未設定 |
| 閲覧 | `http://127.0.0.1:8765/`。本人PCのみ。スマホ対応・外部公開なし |
| 追加契約・利用料金 | 予定なし。PC電気代・通信費、必要なら外付け媒体代 |

コードは同期フォルダでも利用できますが、正本DBはOneDrive等で直接同期しないでください。バックアップはSQLiteのbackup APIで一貫したスナップショットを作り、整合性検査後に原子的に置換します。日次14世代、週次8世代。同日・同週は直近のスナップショットで更新します。部分成功でも保存し、全API失敗時は既存世代を置き換えません。バックアップ失敗は失敗状態として表示します。

同一ディスクのバックアップだけでは故障対策になりません。外付け媒体を指定すると同じ世代管理でもう一組作ります。媒体未接続時はエラーにし、勝手に別の保存先へ切り替えません。クラウド保存・GitHubへのDB送信・GitHub Actionsでの実収集はこの版に含みません。

## 保存条件の扱い

- J-STAGE直取得データは自動運用DBへ保存しません。旧混合DBは転用せず保全します。旧DBからのインポート機能もありません。
- 専用DBには保存条件スナップショットを記録し、従来の`collect`・`sync`による書き込みを拒否します。データファイルを手動編集することまで防ぐセキュリティ境界ではありません。
- CiNiiは本人研究用の書誌。第三者の権利・利用規程に従い、無条件の再配布許諾とは扱いません。
- NDLは指定した提供元への要求と保存条件を実行履歴に残します。CC BYの提供元表示を保持し、提供元一覧へのリンクを案内します。個別書誌の権利表示をすべて機械判定する機能ではありません。
- 本文PDF・画像は取得しません。NDLの利用内容連絡やJSTへの問い合わせは、この実装から送信しません。
- API仕様・条件の変更に備え、運用開始時・更新時に下記公式資料を確認してください。出版活動等で利用目的が変わるときは、営利利用条件も再確認してください。

J-STAGEの必要時閲覧：

```powershell
python -m shiryoshitsu preview --query '姜維' --limit 10
```

SQLiteの`:memory:`と端末表示だけを使い、ファイルDB・ログ・CSVを作りません。終了時に破棄します。端末の履歴保存・画面録画・OSのスワップ等までは制御しません。出力をクラウドログへ転送しないでください。J-STAGEデータのローカル永続保存・バックアップ可否はJSTへの確認が必要です。

公式資料（2026-11-05確認）：

- [J-STAGE WebAPI規約](https://www.jstage.jst.go.jp/static/pages/WebAPI/-char/ja)：第3条第5・6項、第9条。
- [CiNii API規程・関連規程](https://support.nii.ac.jp/ja/cinii/terms)
- [CiNii OpenSearch仕様](https://support.nii.ac.jp/ja/cir/r_opensearch)：`start`・`count`・`sortorder`。公開年は登録日ではありません。
- [NDL提供元別条件](https://ndlsearch.ndl.go.jp/help/api/provider)：指定3提供元とCC BY・注記。
- [NDL API利用案内](https://ndlsearch.ndl.go.jp/help/api)：継続利用の連絡協力、アクセス・クレジット。
- [NDL仕様](https://ndlsearch.ndl.go.jp/help/api/specifications)：OpenSearchの`dpid`・`idx`・`cnt`、先頭500件の制約。

## 承認後の設定順序（今は未実施）

Windows PowerShell 5.1、Python 3.11以上、PCの日時・タイムゾーンは日本時間を想定しています。コードの配置先とPythonの絶対パスを確認してから実施します。以下は操作手順の提示であり、このファイルを読むだけでは設定されません。

1. 公開するコード・文書の差分と費用を承認し、PR・CIで検証する。マージは別途承認。
2. 本人PCでPythonの実体パスを確認する。`Get-Command python`の結果がWindows Storeのエイリアスの場合は実体のPythonを指定する。
3. `Set-CiniiCredential.ps1`でIDを一度入力する。画面には表示せず、本人だけにアクセスを制限した専用フォルダへDPAPI暗号化で保存する。同じWindowsユーザー・同じPCが必要。IDファイルはGitやバックアップに含めない。
4. `Run-Collection.ps1`を手動で1回実行し、実API・保存条件・ID読込・バックアップを確認する。この手順は実収集を行うため、運用開始の承認後に実施する。
5. `Register-Collection.ps1`を`-Enable`なしで実行して、タスク名・保存先・実行ユーザーを確認する。既定はプレビューだけ。
6. 定期実行の明示承認後、同じコマンドへ`-Enable`を付ける。同名タスクは上書きしない。
7. `Open-Library.ps1`へのショートカットを作る。ブラウザ表示がサーバ起動より早い場合は一度再読込する。

例（配置フォルダから。Pythonパスは実機の値に置換）：

```powershell
.\scripts\windows\Set-CiniiCredential.ps1
.\scripts\windows\Run-Collection.ps1 -Python 'C:\実際のパス\python.exe'
.\scripts\windows\Register-Collection.ps1 -Python 'C:\実際のパス\python.exe'
# 最終承認後だけ、上のコマンドに -Enable を追加する。
.\scripts\windows\Open-Library.ps1 -Python 'C:\実際のパス\python.exe'
```

Windowsのスクリプト実行ポリシーで止まった場合は、組織の設定を変更せず、ファイル内容・入手元・PCのポリシーを確認してください。本スクリプトは実行ポリシーを変更しません。タスクスケジューラの時刻はWindows設定に従うため、日本時間であることを確認してください。

停止・解除（データは削除しない）：

```powershell
Disable-ScheduledTask -TaskName 'Shiryoshitsu-Daily'
Unregister-ScheduledTask -TaskName 'Shiryoshitsu-Daily' -Confirm
```

一度だけ再実行する場合は`Run-Collection.ps1 -Force`を使用します。既存の`--db`は`daily`には効かず、`--data-home`で指定した専用DBだけを使います。検索・書出・閲覧時は専用DBを明示します。

```powershell
python -m shiryoshitsu --db "$env:LOCALAPPDATA\Shiryoshitsu\data\catalog.db" recent --days 7
python -m shiryoshitsu --db "$env:LOCALAPPDATA\Shiryoshitsu\data\catalog.db" status
```

## 復元

1. タスクを無効化し、収集と閲覧画面を終了する。
2. 現在の`data`フォルダを別名へ移して保全する。DBだけを上書きして古い`-wal`・`-shm`を残さない。
3. 新しい空の`data`フォルダへ、選んだ`daily-YYYY-MM-DD.db`を`catalog.db`としてコピーする。
4. `stats`・`search`・`status`で確認する。バックアップ後に取得したデータは再収集が必要。
5. CiNii IDは復元対象外。別PCでは再設定する。確認後にタスクを再有効化する。

復元先DBの書誌・整合性確認は自動テスト済みです。Windows上のタスク登録、DPAPI、ポップアップ、実APIとの新しいページ巡回はこのLinux環境では未検証です。既存71テストと追加テストはモック通信で実行しています。実機での確認は運用開始前に必要です。

## 今回の承認対象と未決定事項

今回提示するのは、公開GitHubへ反映できるコード・文書の差分です。DB・ID・実ログは含めません。追加契約・クラウド保存はありません。

PCの実際のコード配置先・Pythonパス・外付け媒体の保存先は未決定です。GitHub反映とは別に、これらを確定してPCへの設定・定期実行の有効化を承認してください。この会話の実行環境から本人PCのタスクを登録したとは扱いません。
