param([string]$DataHome = "$env:LOCALAPPDATA\Shiryoshitsu")
$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') { throw 'Windows用です' }
$secretDir = Join-Path $DataHome 'secrets'
New-Item -ItemType Directory -Force -Path $secretDir | Out-Null
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
& icacls.exe $DataHome /inheritance:r /grant:r "${identity}:(OI)(CI)F" | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'データフォルダのアクセス制限に失敗しました' }
& icacls.exe $secretDir /inheritance:r /grant:r "${identity}:(OI)(CI)F" | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'アクセス制限に失敗しました' }
$secret = Read-Host 'CiNiiのアプリケーションID（画面には表示しません）' -AsSecureString
if ($secret.Length -eq 0) { throw 'IDが空です' }
# Windows DPAPIで暗号化。復号には同じPC・同じWindowsユーザーが必要。
$secret | ConvertFrom-SecureString | Set-Content -LiteralPath (Join-Path $secretDir 'cinii.dpapi') -Encoding ASCII
Write-Host 'IDを暗号化して保存しました。定期実行はまだ登録していません。'
