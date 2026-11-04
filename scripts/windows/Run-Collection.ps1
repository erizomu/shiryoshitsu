param(
    [string]$Python = 'python',
    [string]$DataHome = "$env:LOCALAPPDATA\Shiryoshitsu",
    [string]$BackupDir = '',
    [switch]$Force
)
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$credentialPath = Join-Path $DataHome 'secrets\cinii.dpapi'
$oldId = $env:CINII_APP_ID
$exitCode = 1
try {
    if (Test-Path -LiteralPath $credentialPath) {
        $secure = Get-Content -LiteralPath $credentialPath -Raw | ConvertTo-SecureString
        $env:CINII_APP_ID = ([System.Net.NetworkCredential]::new('', $secure)).Password
    }
    $arguments = @('-m', 'shiryoshitsu', 'daily', '--config', (Join-Path $repo 'topics.json'), '--data-home', $DataHome)
    if ($BackupDir) { $arguments += @('--backup-dir', $BackupDir) }
    if ($Force) { $arguments += '--force' }
    Push-Location -LiteralPath $repo
    try {
        & $Python @arguments
        $exitCode = $LASTEXITCODE
        if ($exitCode -ne 0 -and (Test-Path -LiteralPath (Join-Path $DataHome 'data\catalog.db'))) {
            $stateJson = & $Python -m shiryoshitsu --db (Join-Path $DataHome 'data\catalog.db') status
            $state = $stateJson | ConvertFrom-Json
            if ($state.consecutive_issues -ge 2) {
                $popup = New-Object -ComObject WScript.Shell
                $popup.Popup('史料室の収集で問題が続いています。史料室を開いて前回収集の結果を確認してください。', 10, '史料室', 48) | Out-Null
            }
        }
    }
    finally { Pop-Location }
} catch {
    # 例外本文はIDや外部応答を含む可能性があるため表示しない。
    Write-Error '収集の起動に失敗しました。Python・ID設定・保存先を確認してください。' -ErrorAction Continue
} finally {
    $env:CINII_APP_ID = $oldId
    $secure = $null
}
exit $exitCode
