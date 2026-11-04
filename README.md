# 史料室 — 歴史論文・史料目録の収集システム

PC自動収集の追加案：[`OPERATIONS.md`](OPERATIONS.md)。`daily`による専用DBへの収集・バックアップ、新着・状態表示、Windows設定スクリプトを追加しています。定期実行は自動で有効化されず、登録スクリプトも既定はプレビューです。以下の従来コマンドのDBと、自動運用の専用DBは別です。

J-STAGE、CiNii Research、国立国会図書館サーチ（NDL Search）の公開APIから**書誌メタデータ**を収集し、SQLiteに保存するPython製ツールです。DOIを優先した重複排除、日本語検索、CSV／JSON出力、ローカル閲覧画面を備えています。

**本文PDF・画像・著作物の全文は取得しません。** 収集結果は研究候補の目録です。論文の信頼性、人物の同定、刊本と原史料の区別、掲載内容の適合性は人が確認してください。全件収集や新着の取りこぼしゼロを保証する仕組みではありません。

J-STAGE収録情報は科学技術振興機構（JST）より提供されています：[Powered by J-STAGE](https://www.jstage.jst.go.jp/browse/-char/ja)。本ツールはNDLサーチAPIを使用しています。

## 1. 導入と最初の実行

Python **3.11以上**とSQLiteを使用します。実行・テストともPython標準ライブラリだけで動作し、`pip install`は不要です。Windows／macOS／Linuxで、`topics.json`のあるプロジェクトフォルダを作業ディレクトリにしてください。

```bash
python --version
python -m shiryoshitsu --help
python -m shiryoshitsu init
python -m shiryoshitsu collect --query "姜維" --topic "三国魏晋：姜維" --source jstage,ndl --limit 5
python -m shiryoshitsu search "姜維"
python -m shiryoshitsu stats
```

`python`が使えない環境では`python3`、Windowsでは`py -3`に置き換えます。API収集にはインターネット接続が必要です。DB初期化、保存済みデータの検索・出力、ローカル閲覧、テストは外部APIへの接続なしで利用できます。

初期DBは`data/shiryoshitsu.db`です。保存先を変更する場合、`--db`をサブコマンドより前に指定します。

```bash
python -m shiryoshitsu --db data/history.db init
python -m shiryoshitsu --db data/history.db search "鄧艾"
```

## 2. CiNii Researchの設定

CiNii Research OpenSearch v2には、[API利用登録](https://support.nii.ac.jp/ja/cinii/api/developer)で発行されるアプリケーションIDが必要です。[公式登録フォーム](https://api.ci.nii.ac.jp/ja/)で利用者情報を入力して規約を確認し、取得したIDを環境変数`CINII_APP_ID`に設定してください。ID未設定の場合はCiNiiだけをスキップします。登録前は`--source jstage,ndl`を指定すると対象を明示的に限定できます。

macOS／Linux：

```bash
export CINII_APP_ID='発行されたID'
python -m shiryoshitsu collect --query "鄧艾" --topic "三国魏晋：鄧艾" --limit 10
```

Windows PowerShell：

```powershell
$env:CINII_APP_ID = '発行されたID'
py -3 -m shiryoshitsu collect --query '鄧艾' --topic '三国魏晋：鄧艾' --limit 10
```

設定は通常そのターミナルのプロセスに限り有効です。`.env.example`は設定項目の見本であり、**本ツールは`.env`を自動読込しません**。本物のIDをコード、`topics.json`、公開リポジトリ、共有ログに書かないでください。書誌リンクから認証用クエリを除去し、診断にもIDを出さない設計です。

## 3. 単語・テーマを収集する

```bash
python -m shiryoshitsu collect --query "石苞" --topic "三国魏晋：石苞" --source jstage,ndl --limit 10
python -m shiryoshitsu sync --config topics.json --source jstage,ndl --limit 10
```

`sync`は`topics.json`の全テーマ・検索語を順番に処理します。初期テーマは姜維、鄧艾、石苞、戦国期松下氏、山西省残留日本兵、言継卿記・山科言継、囲碁史です。検索語の追加例：

```json
{"name": "三国魏晋：鄧艾", "queries": ["鄧艾", "邓艾"]}
```

| オプション | 初期値・意味 |
|---|---|
| `--source` | `all`。`jstage,cinii,ndl`からカンマ区切りで指定 |
| `--limit` | `10`。各検索語・APIについて先頭1ページの最大件数、1〜100 |
| `--delay` | `2`秒。API要求間の最低待機時間。内部再試行にも適用。0以上の有限値 |
| `--timeout` | `15`秒。HTTPタイムアウト。正の有限値 |
| `--log-file` | `logs/collect.jsonl`。収集の診断を追記するJSON Linesファイル |

検索条件はJ-STAGEが論文タイトル（`article`）、CiNiiがフリーワード（`q`）、NDLが全項目（`any`）です。同じ語でも各サービスの検索対象・順位は異なります。**ページを巡回して全件を取得する機能はありません。** 年による収集条件の分割や差分取得も未実装です。`search --from-year`は保存済みデータに対する絞り込みです。

通信は逐次実行します。一時的なネットワーク障害、HTTP 429や一部5xxは待機して最大2回再試行します。`Retry-After`が60秒を超える場合は早期再送せず今回の取得を終了します。失敗した収集先は日本語の診断を記録して次へ進み、不正な書誌も記録して除外します。ログファイルに書き込めない場合は標準エラー出力に記録して継続します。XMLのDTD・外部実体は拒否します。HTTP成功でもAPIエラーや想定外のHTML応答を正常データとして保存しません。J-STAGEの検索結果0件は正常として扱います。

収集コマンドの終了コードは`0`が全対象成功、`2`が一部成功（失敗・不正書誌・未設定によるスキップを含む）、`1`が成功した収集先なし、または設定等のエラーです。APIの0件応答は成功に含みます。監視や将来のスケジューラでは終了コードと診断ログを確認してください。取得制限が続く場合は間隔を広げ、提供元の利用条件を確認します。初期値2秒は本ツールの設定であり、機関が保証する許容レートではありません。

## 4. 保存・重複排除・検索・書出

書誌、出典、研究テーマ、検索語の履歴をSQLiteに保存します。出典にはAPI機関、元の書誌ID、元URL、取得日時を保持します。複数の検索語やテーマで再収集しても過去の検索履歴を上書きしません。APIが返すデータ提供機関情報も保持しますが、欠落情報を推定して補いません。

重複判定は次の方針です。

1. 同じDOIを優先して同定します。異なるDOIは同じタイトルでも統合しません。
2. DOIのない側がある場合に限り、正規化したタイトル・出版年・第一著者の完全一致を照合します。候補が複数ある場合は自動統合しません。
3. 同じ収集元の同じIDは再取得として扱います。ただし、そのIDのDOIが以前と衝突した場合は記録して拒否し、既存情報を保全します。

既存の値は原則保持し、欠けている項目を補います。旧初期版DBは履歴用テーブル等を追加して移行します。旧版が過去に上書きした検索語は復元できません。表記差や情報不足によって別レコードが残ることがあります。

```bash
python -m shiryoshitsu search "鄧艾"
python -m shiryoshitsu search "松下" --topic "戦国：松下氏" --source ndl
python -m shiryoshitsu search "姜維" --from-year 2000 --limit 100 --json
python -m shiryoshitsu stats
python -m shiryoshitsu export --format csv --out exports/bibliography.csv
python -m shiryoshitsu export --format json --out exports/bibliography.json
python -m shiryoshitsu export --query "囲碁" --format json --out exports/go-history.json
```

検索はタイトル・著者・刊行物名・DOI・テーマなどの部分一致です。`%`や`_`はワイルドカードではなく文字として扱います。エクスポートは条件に合う全件が対象で、1万件で打ち切りません。ただしメモリに読み込むため、大規模DBでは必要なメモリ量が増えます。

JSONはUTF-8、CSVはExcelで日本語を表示しやすいUTF-8 BOM付きです。CSVの`sources`列には元ID・URL・検索履歴等をJSON文字列で保持します。CSVの数式として解釈され得るセルには先頭にアポストロフィを付けます。未加工の値を再利用する場合はJSONを使ってください。出力ファイルにも書誌・検索履歴が含まれるため、共有時は内容と提供元の利用条件を確認してください。

## 5. ローカル閲覧画面

```bash
python -m shiryoshitsu serve
```

ブラウザで **http://127.0.0.1:8765/** を開きます。表示は最大200件で、検索と提供元ページへのリンクを利用できます。`Ctrl+C`で終了します。

`--host`は`127.0.0.1`または`localhost`だけを受け付け、必ずIPv4ループバックへ接続待機します。`0.0.0.0`、LAN・公開アドレス、IPv6は拒否します。`--port`は1〜65535です。Hostヘッダの検査、HTMLエスケープ、リンクのスキーム検査を行います。

認証はなく、インターネット公開用サーバではありません。リバースプロキシ、トンネル、ポート転送で公開しないでください。同じPCを利用できる他のユーザーからの閲覧を防ぐ機能もありません。

## 6. オフラインテストとGitHub Actions

```bash
python -m unittest discover -s tests -v
```

架空の書誌とモック応答を用い、3機関のXMLパーサ、危険なXML、DOI・DOIなしの照合、異なるDOIの保持、日本語検索、CLI、CSV／JSON、ローカル画面を検証します。テストは外部APIへ通信せず、CiNii IDも不要です。

`.github/workflows/tests.yml`はUbuntu／WindowsとPython 3.11／3.12／3.13の組合せで同じコマンドを実行する設定です。承認後にGitHubへ反映するとpush／PRでテストが動作します。定期収集・デプロイ用のワークフローはありません。

オフラインテストの成功と実APIへの通信成功は別です。実通信の少量確認は`collect --source jstage,ndl --limit 1`などで行えます。CiNiiは発行済みIDが必要です。今回の実行環境、テスト結果、通信確認、残課題は[検証記録](VERIFICATION.md)を参照してください。

## 7. API仕様・利用条件

以下は**2026-11-03確認**です。運用開始時・継続運用時にはリンク先の更新を確認してください。取得したメタデータの利用条件は、ソースコードの公開範囲とは別に適用されます。

| 機関 | 本実装のAPI | 公式資料 |
|---|---|---|
| J-STAGE | `https://api.jstage.jst.go.jp/searchapi/do`、`service=3`、Atom XML | [WebAPI紹介](https://www.jstage.jst.go.jp/static/pages/JstageServices/TAB3/-char/ja)、[マニュアル ver.2.0（2026-03-26）](https://www.jstage.jst.go.jp/static/files/ja/manual_api.pdf)、[利用規約](https://www.jstage.jst.go.jp/static/pages/WebAPI/-char/ja) |
| CiNii Research | `https://cir.nii.ac.jp/opensearch/v2/articles`、`format=atom`、`appid`必須 | [OpenSearch v2仕様](https://support.nii.ac.jp/ja/cir/r_opensearch)、[API登録・利用条件](https://support.nii.ac.jp/ja/cinii/api/developer) |
| NDLサーチ | `https://ndlsearch.ndl.go.jp/api/opensearch`、拡張RSS 2.0 | [仕様書 第1.5版（2026-09-25）](https://ndlsearch.ndl.go.jp/file/help/api/specifications/ndlsearch_api_20260925.pdf)、[API利用条件](https://ndlsearch.ndl.go.jp/help/api)、[提供元別条件](https://ndlsearch.ndl.go.jp/help/api/provider) |

- **J-STAGE**：非営利目的の利用申請は不要です。API利用規約に従い、リンク付きの「Powered by J-STAGE」とJST提供の表示を保持します。大量ダウンロードは制限されます。商用利用にはサービスの有償・無償にかかわらず申請が必要です。第3条第5項にはAPI取得情報をサーバ・クラウド等で24時間以上保存・キャッシュしない条件があり、最新情報の表示条件もあります。本実装には保存期限の自動管理機能がないため、J-STAGEの取得DBをそのままサーバ・クラウドへ永続化する用途には使わず、利用形態に応じてJSTへ確認してください。
- **CiNii**：登録と関連規程への同意が必要です。短時間の大量アクセスは遮断される場合があります。商用サイトでの利用は事前に相談してください。公開リンクに`appid`を含めません。
- **NDL**：OpenSearchは一度に最大500件、ページ指定を変えても501件目以降は取得できません。本実装はさらに先頭1〜100件に限定します。NDLは同時アクセスを避けるよう求め、具体的な数値レート上限は公表していません。非営利利用でも提供元別の条件・クレジットを確認してください。営利利用には申請・許諾が必要な場合があります。継続アクセスでは利用内容の連絡への協力が求められています。

APIが返す出版社・提供機関・ライセンス情報は不完全な場合があります。データの再配布・公開時は元の書誌ページと提供機関の条件を確認し、必要な帰属表示・ライセンスリンクを補ってください。個別の許諾を自動判定する機能はありません。

## 8. 費用・永続化・将来の定期収集

本実装の実行に有料ライブラリや外部サーバの契約は不要です。ローカル運用には既存PCの電気・通信・ディスク・バックアップの費用がかかります。APIの課金契約や支払設定は行いません。APIは各機関の登録・許諾・利用条件の範囲で使い、提供条件の変更にも従ってください。

既存の**公開リポジトリ**で標準GitHub-hosted runnerを使うActions実行は無料です。本CIは`ubuntu-latest`／`windows-latest`を使用し、有料のlarger runnerや成果物・キャッシュ保存は設定しません。非公開化、runner変更、保存サービス追加時は費用を再確認してください。[GitHub公式料金説明（2026-11-03確認）](https://docs.github.com/en/billing/concepts/product-billing/github-actions)

定期収集は将来、承認を得たうえでWindowsタスクスケジューラ、macOS launchd、Linux cronから`python -m shiryoshitsu sync`を呼び出す方法で実現できます。作業フォルダとPythonの絶対パスを固定し、前回の処理と重複起動しないようにします。実行時にPCが起動していること、CiNii用環境変数が渡ること、終了コードとログを監視することが必要です。現在スケジュールは登録していません。

GitHubへコードを置くだけではSQLiteデータは保存されません。GitHub-hosted runnerの作業領域は実行ごとに破棄されるため、Actionsで定期収集するなら各APIの保存条件を確認したうえで非公開の保存先・保存期限・復元・排他制御・秘密情報管理・費用の設計が別途必要です。特にJ-STAGEの上記保存制限があるため、全機関のデータを一律にクラウドへ蓄積する構成にはできません。Actionsのキャッシュや期限付き成果物を唯一のDB保管先にはしないでください。DBを公開リポジトリにコミットする運用は採りません。

DBのバックアップは収集・閲覧プロセスを停止して接続を閉じてから行うか、SQLiteのバックアップAPIを使ってください。稼働中の`.db`だけをコピーするとWAL内の未反映データが欠けることがあります。旧版DBを初めて開く前にもバックアップを推奨します。

## 9. ファイルと公開承認

```
shiryoshitsu/             # HTTP、パーサ、モデル、SQLite、CLI、Web画面
tests/                   # 外部API通信なしの自動テスト
.github/workflows/       # テスト専用CI
topics.json              # 研究テーマ・検索語
.env.example             # 設定項目の例（自動読込なし）
.gitignore               # DB・秘密情報・収集データ等を除外
pyproject.toml           # Pythonパッケージ情報
VERIFICATION.md          # 今回の検証結果
```

秘密情報、個人情報、ローカルDB、収集したメタデータやログ、著作物の全文はコミットしません。`.gitignore`だけに依存せず、公開前に差分を確認してください。

公開先は既存の公開リポジトリ **[erizomu/shiryoshitsu](https://github.com/erizomu/shiryoshitsu)** です。変更内容・公開先・費用を事前提示し、明示的な承認が得られるまで、ブランチのpush、PR作成・マージ、GitHub Pages公開、定期実行の有効化、外部サーバへのデプロイは行いません。承認後は作業ブランチからPRを作り、CI成功を確認します。`main`を直接更新せず、マージは別途確認します。
