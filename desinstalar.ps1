<#
  Remove o Soom deste usuário: fecha o app, tira do início automático e do Menu Iniciar e apaga
  %LOCALAPPDATA%\Soom (programa, Python do app e configurações).

  Uso: dois cliques em "Desinstalar.cmd" (ou rode este script no PowerShell).
  Opção:
    -RemoverModelo   apaga também o modelo de voz baixado (~3 GB)
  O Python 3.12 continua instalado (outros programas podem usar).
#>
param([switch]$RemoverModelo)

$ErrorActionPreference = 'Stop'
$SoomHome = Join-Path $env:LOCALAPPDATA "Soom"
Set-Location $env:TEMP   # não ficar "dentro" da pasta que vai ser apagada

Write-Host "==> Fechando o Soom" -ForegroundColor Cyan
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" |
    Where-Object { $_.CommandLine -match 'Soom\.pyw' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Milliseconds 500

Write-Host "==> Tirando do início automático e do Menu Iniciar" -ForegroundColor Cyan
Remove-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" -Name "Soom" -ErrorAction SilentlyContinue
$shortcut = Join-Path ([Environment]::GetFolderPath('Programs')) "Soom.lnk"
if (Test-Path $shortcut) { Remove-Item $shortcut -Force }

Write-Host "==> Apagando $SoomHome" -ForegroundColor Cyan
if (Test-Path $SoomHome) { Remove-Item $SoomHome -Recurse -Force }

if ($RemoverModelo) {
    $model = Join-Path $env:USERPROFILE ".cache\huggingface\hub\models--onnx-community--whisper-large-v3-turbo"
    Write-Host "==> Apagando o modelo de voz" -ForegroundColor Cyan
    if (Test-Path $model) { Remove-Item $model -Recurse -Force }
}

Write-Host ""
Write-Host "O Soom foi removido. O Win+H voltou a ser o do Windows." -ForegroundColor Green
