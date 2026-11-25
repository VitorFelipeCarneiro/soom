<#
  Instala o Soom (ditado por voz com IA, 100% local) para o seu usuário do Windows:
    1. Python 3.12 (via winget, só se ainda não estiver instalado)
    2. Copia o programa para %LOCALAPPDATA%\Soom\app
    3. Python do app (%LOCALAPPDATA%\Soom\venv) com as bibliotecas de requirements.txt
    4. Modelo de voz Whisper large-v3-turbo (~3 GB, só na primeira vez)
    5. Atalho no Menu Iniciar e início automático junto com o Windows
    6. Abre o Soom

  Uso: dois cliques em "Instalar.cmd" (ou rode este script no PowerShell).
  Opções:
    -SemIniciarComWindows   não abre o Soom junto com o Windows
    -PularModelo            não baixa/testa o modelo agora (ele é baixado na primeira abertura)
#>
param(
    [switch]$SemIniciarComWindows,
    [switch]$PularModelo
)

$ErrorActionPreference = 'Stop'
$Src = $PSScriptRoot
$SoomHome = Join-Path $env:LOCALAPPDATA "Soom"
$OldHome = Join-Path $env:LOCALAPPDATA "DitadoIA"   # nome antigo do projeto
$AppDir = Join-Path $SoomHome "app"
$VenvDir = Join-Path $SoomHome "venv"
$venvPy = Join-Path $VenvDir "Scripts\python.exe"
$pyw = Join-Path $VenvDir "Scripts\pythonw.exe"
$launcher = Join-Path $AppDir "Soom.pyw"
$RunKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"

function Step($msg) { Write-Host ""; Write-Host "==> $msg" -ForegroundColor Cyan }
function Check($what) { if ($LASTEXITCODE -ne 0) { throw "$what falhou (código $LASTEXITCODE)" } }

function Stop-Soom {
    Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" |
        Where-Object { $_.CommandLine -match 'Soom\.pyw|ditado\.py' } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Milliseconds 500
}

function Find-Python312 {
    foreach ($c in @("$env:LOCALAPPDATA\Programs\Python\Python312\python.exe", "$env:ProgramFiles\Python312\python.exe")) {
        if (Test-Path $c) { return $c }
    }
    return $null
}

Step "Fechando o Soom (se estiver aberto)"
Stop-Soom

if ((Test-Path $OldHome) -and -not (Test-Path $SoomHome)) {
    Step "Aproveitando os dados da versão antiga (Ditado IA)"
    Rename-Item $OldHome $SoomHome
}
New-Item -ItemType Directory -Force $SoomHome | Out-Null
foreach ($old in @("ditado.log", "ditado.log.1", "ditado.ico")) {   # arquivos com o nome antigo
    $path = Join-Path $SoomHome $old
    if (Test-Path $path) { Remove-Item $path -Force }
}

Step "Procurando o Python 3.12"
$py = Find-Python312
if (-not $py) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw "Falta o Python 3.12 e o winget não está disponível. Instale o Python 3.12 em https://www.python.org/downloads/ (marque 'Add python.exe to PATH') e rode o instalador do Soom de novo."
    }
    Write-Host "Python 3.12 não encontrado. Instalando pelo winget (python.org)..."
    winget install --id Python.Python.3.12 -e --scope user --silent --accept-package-agreements --accept-source-agreements
    Check "Instalação do Python"
    $py = Find-Python312
    if (-not $py) { throw "Python instalado, mas não encontrei o python.exe. Feche e abra o PowerShell e rode de novo." }
}
Write-Host "Usando $py"

Step "Copiando o programa para $AppDir"
$sameFolder = (Resolve-Path $Src).Path.TrimEnd('\') -ieq $AppDir.TrimEnd('\')
if (-not $sameFolder) {
    if (Test-Path $AppDir) { Remove-Item $AppDir -Recurse -Force }
    New-Item -ItemType Directory -Force $AppDir | Out-Null
    foreach ($item in @("Soom.pyw", "soom", "requirements.txt", "README.md", "instalar.ps1", "desinstalar.ps1", "Instalar.cmd", "Desinstalar.cmd")) {
        $path = Join-Path $Src $item
        if (Test-Path $path) { Copy-Item $path -Destination $AppDir -Recurse -Force }
    }
    Get-ChildItem $AppDir -Recurse -Directory -Filter __pycache__ | Remove-Item -Recurse -Force
}

if (-not (Test-Path $venvPy)) {
    Step "Criando o Python do app em $VenvDir"
    & $py -m venv $VenvDir
    Check "Criação do ambiente Python"
}

Step "Instalando as bibliotecas"
& $venvPy -m pip install --upgrade pip --disable-pip-version-check --quiet
Check "Atualização do pip"
& $venvPy -m pip install -r (Join-Path $AppDir "requirements.txt") --disable-pip-version-check --quiet
Check "Instalação das bibliotecas"

if (-not $PularModelo) {
    Step "Baixando e testando o modelo de voz (na primeira vez são ~3 GB)"
    & $venvPy $launcher --baixar-modelo
    Check "Download do modelo"
}

Step "Criando o atalho no Menu Iniciar"
& $venvPy $launcher --gerar-icone
Check "Criação do ícone"
$programs = [Environment]::GetFolderPath('Programs')
$startupFolder = [Environment]::GetFolderPath('Startup')
foreach ($old in @("$programs\Ditado IA.lnk", "$startupFolder\Ditado IA.lnk", "$startupFolder\Soom.lnk", (Join-Path $Src "Ditado IA.lnk"))) {
    if (Test-Path $old) { Remove-Item $old -Force }
}
$ws = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut("$programs\Soom.lnk")
$lnk.TargetPath = $pyw
$lnk.Arguments = "`"$launcher`""
$lnk.WorkingDirectory = $AppDir
$lnk.IconLocation = Join-Path $SoomHome "soom.ico"
$lnk.Description = "Soom - ditado por voz com IA, 100% local"
$lnk.Save()
Write-Host "Atalho: $programs\Soom.lnk"

$configPath = Join-Path $SoomHome "config.json"
if ($SemIniciarComWindows) {
    Step "Início automático: desligado (você pode ligar pelo ícone do Soom)"
    Remove-ItemProperty -Path $RunKey -Name "Soom" -ErrorAction SilentlyContinue
    $cfg = if (Test-Path $configPath) { Get-Content $configPath -Raw -Encoding UTF8 | ConvertFrom-Json } else { [pscustomobject]@{} }
    $cfg | Add-Member -NotePropertyName iniciar_com_windows -NotePropertyValue $false -Force
    [IO.File]::WriteAllText($configPath, ($cfg | ConvertTo-Json -Depth 5), (New-Object Text.UTF8Encoding $false))
} else {
    Step "Início automático: o Soom vai abrir junto com o Windows"
    Set-ItemProperty -Path $RunKey -Name "Soom" -Value "`"$pyw`" `"$launcher`""
}

Step "Abrindo o Soom"
Start-Process -FilePath $pyw -ArgumentList "`"$launcher`"" -WorkingDirectory $AppDir

Write-Host ""
Write-Host "Pronto! O Soom está aberto (ícone perto do relógio)." -ForegroundColor Green
Write-Host "Aperte Win+H para começar a ditar e Win+H de novo para terminar."
Write-Host "Para trocar o atalho ou sair, clique com o botão direito no ícone do Soom."
