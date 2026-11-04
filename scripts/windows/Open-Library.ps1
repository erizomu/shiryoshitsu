param([string]$Python = 'python', [string]$DataHome = "$env:LOCALAPPDATA\Shiryoshitsu")
$ErrorActionPreference = 'Stop'
$db = Join-Path $DataHome 'data\catalog.db'
if (-not (Test-Path -LiteralPath $db)) { throw '収集DBがありません。初回収集を先に実行してください。' }
Push-Location -LiteralPath (Join-Path $PSScriptRoot '..\..')
try {
    # ブラウザは起動直後、サーバ準備前になる場合があります。その場合は再読込してください。
    Start-Process 'http://127.0.0.1:8765/?new=1'
    & $Python -m shiryoshitsu --db $db serve
} finally { Pop-Location }
