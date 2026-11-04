param(
    [Parameter(Mandatory=$true)][string]$Python,
    [string]$DataHome = "$env:LOCALAPPDATA\Shiryoshitsu",
    [string]$BackupDir = '',
    [switch]$Enable
)
$ErrorActionPreference = 'Stop'
# 既定はプレビュー。明示承認後に利用者が-Enableを付けて登録する。
$pythonPath = (Resolve-Path -LiteralPath $Python).Path
$dataPath = [System.IO.Path]::GetFullPath($DataHome)
$runner = Join-Path $PSScriptRoot 'Run-Collection.ps1'
$powershell = Join-Path $PSHOME 'powershell.exe'
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
foreach ($value in @($pythonPath, $dataPath, $runner, $BackupDir)) {
    if ($value.Contains('"') -or $value.Contains("`n") -or $value.Contains("`r")) { throw 'パスに引用符や改行は使えません' }
}
$taskName = 'Shiryoshitsu-Daily'
$argLine = '-NoProfile -NonInteractive -File "{0}" -Python "{1}" -DataHome "{2}"' -f $runner, $pythonPath, $dataPath
if ($BackupDir) { $argLine += ' -BackupDir "{0}"' -f ([System.IO.Path]::GetFullPath($BackupDir)) }
Write-Host "変更: タスク $taskName / 毎日19:17＋ログオン時 / 同時実行なし"
Write-Host "実行ユーザー: $identity（ログオン中のみ）"
Write-Host "保存先: $dataPath / 追加バックアップ: $BackupDir"
Write-Host '公開: PC内のみ。GitHubへの送信なし。追加契約・課金なし。'
if (-not $Enable) { Write-Host 'プレビューのみ。登録していません。'; return }
if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) { throw '同名タスクが存在します。上書きしません。' }
if (-not (Test-Path -LiteralPath (Join-Path $dataPath 'secrets\cinii.dpapi'))) { throw '先にCiNii IDを設定してください' }
$action = New-ScheduledTaskAction -Execute $powershell -Argument $argLine
$triggers = @((New-ScheduledTaskTrigger -Daily -At '19:17'), (New-ScheduledTaskTrigger -AtLogOn -User $identity))
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 1) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $triggers -Settings $settings -Principal $principal -Description '史料室: 本人PCの専用DBへ書誌収集。日単位で重複実行を回避。' | Out-Null
Write-Host '登録しました。PCの日時・タイムゾーンが日本時間であることを確認してください。'
