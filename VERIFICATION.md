# 実装・検証記録（2026-11-03実装／2026-11-04追記）

## PC自動収集の追加案（2026-11-05、ローカル検証のみ）

基準main：`2a42f963add6246d0f54c8ce6121b5e3f5f0cb7d`。既存実装を再利用し、専用DBへのCiNii・条件確認済みNDL収集、最大5ページの走査、J-STAGEメモリ内閲覧、新着・稼働状況、SQLite backup APIと日次／週次世代管理、Windows用設定スクリプトを追加しました。詳細・保存先・費用・承認対象は[`OPERATIONS.md`](OPERATIONS.md)に記載しています。

- この追加作業ではGitHubへのpush・PR作成・マージ、タスク登録、有料契約、実API収集を行っていません。
- Linux／Python 3.12.14で`python -m unittest discover -s tests -v`：**92件成功（既存71＋追加21）**。
- 追加検証：専用DBへの手動書込み拒否、既存混合DBの転用拒否、提供元・ページ上限、ページ反復検出、既知書誌後の巡回、欠落ID・API失敗継続、秘密値と応答本文の非記録、WALを含むバックアップと復元、14日次・8週次世代、重複起動ロック、同じ予定分の重複防止、朝の補完と当夜分の分離、バックアップ失敗、新着の初回取得日判定、画面のエスケープ、J-STAGE一時閲覧のファイル非作成。
- 通信はすべてモックです。新しいページ巡回の実API疎通、Windows DPAPI・アクセス権設定・タスクスケジューラ・通知は未検証です。既存mainの過去の疎通記録と区別してください。WindowsスクリプトはPowerShell 5.1向けにUTF-8 BOM付きです。
- 自動収集の既定は19検索語×4対象、1回100件×最大5ページです。完全な差分取得・全件収集は未実装です。公開年で除外せず、毎回同じ範囲を重ねて走査します。上限・失敗は記録と画面に残します。
- 同一PC上のバックアップだけでは媒体故障を防げません。外付けの追加保存先・実際のPythonパス・コード配置先は未確定です。スマホ閲覧・クラウドDB保存・Actions実収集は追加していません。

以下は初期版の過去の検証記録です。

本書は2026-11-03の公開承認前のローカル検証と、2026-11-04の追加確認・公開作業の記録です。ユーザー承認を得て作業ブランチをpushし、[PR #1](https://github.com/erizomu/shiryoshitsu/pull/1)を作成しました。CIの最新結果は[PRのChecks](https://github.com/erizomu/shiryoshitsu/pull/1/checks)および[Actions](https://github.com/erizomu/shiryoshitsu/actions)を参照してください。マージ・定期収集・外部公開は別途承認の対象です。

## 対象と作業状態

- 対象：既存の公開リポジトリ `erizomu/shiryoshitsu`。
- 基準：`main` の `d1af979e6374244f833ad2d792a21d602db0b134`（READMEのみ）。
- 提供された `shiryoshitsu_codex_handoff_2026-11-03.zip` 内の実装をベースに修正。新規に作り直したものではありません。
- 作業ブランチ：`codex/history-collection-mvp`。2026-11-03の初回検証時点ではコミット・push・PRは未実施でしたが、2026-11-04に作業ブランチをpushしてPR #1を作成しました。mainへのマージ、Web公開、定期実行は行っていません。
- ローカル実行環境：Windows、Python 3.12.14、標準ライブラリのみ。Ubuntu／WindowsとPython 3.11／3.12／3.13の6環境はPRのCI対象です。本書のローカルテスト結果とCI結果は区別し、最新コミットに対する成功・失敗は上記Checksで確認してください。

## 既存実装の確認と差分

提供された20テストをこの環境で実行すると、19件成功、JSONをOS既定のcp932で読み込むテスト1件が失敗しました。UTF-8を明示して修正し、以下の回帰テストを追加しました。

| ファイル群 | 主な変更 |
|---|---|
| `shiryoshitsu/providers.py` | 3機関の仕様照合、CiNii v2維持、J-STAGEの0件・上限警告処理、DTD／実体拒否、誤応答検出、安定ID必須、NDL出版年と提供機関 |
| `shiryoshitsu/models.py`・`storage.py` | DOIの厳格な抽出、異なるDOIと曖昧な候補の誤統合防止、同一出典IDのDOI矛盾拒否、元ID・URL・全検索語・テーマ履歴保存、既存DBのschema v2移行 |
| `shiryoshitsu/client.py`・`cli.py` | 有限再試行、待機間隔とRetry-After、壊れたHTTP応答処理、秘密値を伏せた診断ログ、部分失敗継続、設定検証、日本語ヘルプ、全件出力、CSVの出典情報・数式対策 |
| `shiryoshitsu/webui.py` | ループバック専用、Host検査、HTMLエスケープとURL再検証、提供元表示 |
| `tests/` | 既存テストの修正とCLI・HTTP・重複排除・移行・パーサ・Webの回帰テスト追加 |
| `README.md`・`.env.example`・`.gitignore` | 日本語手順、最新公式資料、費用と運用制約、ID設定方法、秘密情報・DB・出力・ログの除外 |
| `topics.json` | 指定7テーマを維持し、中国語簡体字・異体字の検索語を追加 |
| `.github/workflows/tests.yml` | Ubuntu／Windows × Python 3.11／3.12／3.13のオフラインテスト。公式Actions v6のSHAを固定。scheduleなし |

基準のmainにはREADMEのみだったため、PRの差分はREADMEの置換と実装・設定・テスト・文書の追加です。引き継ぎZIPにあった `AGENTS.md`・`CODEX_TASK.md` も保持しています。

## オフライン自動テスト

```bash
python -m unittest discover -s tests -v
```

**71テスト成功（失敗・エラー0件）。** 通信・待機はモック化し、Webもソケットを開かずメモリ上のHTTP要求で検証しています。テストの架空書誌には実際の収集データを使っていません。

初回CIではUbuntuの3環境が成功し、Windowsの3環境でテスト内の日本語メッセージ出力がcp1252により失敗しました。エクスポートテストで標準出力を捕捉して内容も検証する形に修正し、ローカルでも`PYTHONIOENCODING=cp1252`で71テストの成功を確認しました。最終コミットのCI結果は上記PRのChecksを参照してください。

検証範囲は3機関のXML、正常0件、APIエラー、不正XML、UTF-16等のDTD／実体宣言、DOI一致、DOIなし一致、異なるDOI、曖昧な候補、日本語検索、移行と履歴保存、CSV／JSON、CLIの失敗継続と秘密値除去、Webの検索・エスケープ・公開アドレス拒否です。

## 外部APIへの少量疎通（自動テストとは別）

2026-11-03に検索語「歴史」、各機関の取得上限1件で逐次実行しました。最初の制限環境では接続できませんでしたが、ネットワークアクセス可能な実行で以下を確認しました。

```bash
python -m shiryoshitsu --db data/api-smoke.db collect --query "歴史" --source all --limit 1 --delay 2 --timeout 15 --log-file logs/api-smoke.jsonl
```

| 取得元 | 結果 |
|---|---|
| J-STAGE | 実応答から1件を解析・保存。`WARN_002`は警告として扱い、題名・著者・出版年・DOI・URLを確認 |
| NDLサーチ | 実応答から1件を解析・保存。題名・出版年・元ID・URLを確認 |
| CiNii Research | `CINII_APP_ID`未設定のため未通信。v2の要求パラメータとAtomパーサはオフラインテスト済み |

DBは2書誌・2出典・失敗0件・スキップ1件でした。検索語は疎通用であり、取得結果の歴史研究への適合性を保証するものではありません。この確認は全件取得、継続稼働、将来のAPI互換性を保証しません。

### CiNii追加確認（2026-11-04、日本時間）

API利用登録済みのIDを、その実行プロセスの環境変数にだけ設定して確認しました。IDの入力は非表示とし、コード・設定ファイル・コマンド本文・GitHubには記録していません。

```bash
python -m shiryoshitsu --db data/cinii-smoke.db collect --query "姜維" --source cinii --limit 1 --timeout 15 --log-file ""
python -m shiryoshitsu --db data/cinii-smoke.db search "姜維" --json
```

**実取得1件・新規書誌1件・新規出典1件・失敗0件・スキップ0件、終了コード0。** 題名・著者・出版年・掲載誌・頁・元ID・URL・検索語が保存され、日本語検索でも取得できることを確認しました。これで3機関すべての少量実通信・解析・SQLite保存を確認済みです。実収集DBはGitの除外対象です。

## ローカルCLIの確認

以下を実行し、DB初期化、日本語検索、統計、2件のCSV／JSON書出に成功しました。

```bash
python -m shiryoshitsu --db data/cli-check.db init
python -m shiryoshitsu --db data/api-smoke.db search "歴史" --json
python -m shiryoshitsu --db data/api-smoke.db stats
python -m shiryoshitsu --db data/api-smoke.db export --format csv --out exports/api-smoke.csv
python -m shiryoshitsu --db data/api-smoke.db export --format json --out exports/api-smoke.json
```

DB、実収集データ、CSV／JSON、診断ログはGitの除外対象であることを確認しました。配布ZIP・PRの差分には含めていません。`git diff --check`も成功しています。

## 公開作業の経過と残課題

- CiNiiの少量実通信は2026-11-04に成功しました。別のターミナルで利用する際は、環境変数`CINII_APP_ID`を再設定してください。
- GitHub Actionsは作業ブランチのpush／PRにより開始されました。6環境の最新結果は[PRのChecks](https://github.com/erizomu/shiryoshitsu/pull/1/checks)で確認してください。本書では実行中のCIを成功扱いにしていません。
- 2026-11-04の当初の公開試行では、GitHub連携の書き込みが403となり、PC側Gitにも認証情報がありませんでした。その後、ユーザーがGit Credential Managerのログインを完了し、Git認証が成功しました。作業ブランチのpushと[PR #1](https://github.com/erizomu/shiryoshitsu/pull/1)の作成は完了しています。
- 各検索語につき先頭1〜100件を取得する初期版です。ページ巡回・差分取得・研究分野の自動判定は未実装です。
- 旧版で失われた検索履歴や過去の誤統合は自動復元できません。移行前にDBをバックアップしてください。
- 定期収集とデータ永続化はREADMEに将来の方法・制約を記載しただけで、有効化していません。
- 公開準備時にJ-STAGE規約を再確認し、リンク付きクレジットと利用規約リンク、サーバ・クラウド等での24時間以上の保存制限を反映しました。本実装は保存期限を自動管理しないため、J-STAGE収集DBをそのままクラウドへ永続化する用途には使えません。

公開先は既存の公開GitHubリポジトリ `erizomu/shiryoshitsu` の作業ブランチとPR #1です。追加の有料サービスや支払設定は不要で、標準GitHub-hosted runnerによる公開リポジトリのテストは無料です（公式料金へのリンクはREADME参照）。ローカル運用の電気・通信・ディスク等は利用者の環境に依存します。

作業ブランチへの反映・PR作成・CI確認はユーザー承認済みで、認証待ちは解消しています。mainは直接更新していません。マージ・定期実行・デプロイは別途確認します。
