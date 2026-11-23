param(
    [ValidateSet('Debug', 'Release')][string]$Modo = 'Debug',
    [switch]$Executavel,
    [switch]$SemBuild,
    [switch]$BancoTemporario
)
$ErrorActionPreference = 'Stop'
$ediskRaiz = Split-Path -Parent $PSScriptRoot
$ediskVariaveis = @('EDISK_DEV_ROOT', 'EDISK_ENV', 'EDISK_TEST_ROOT')
$ediskAnterior = @{}
foreach ($ediskNome in $ediskVariaveis) {
    $ediskAnterior[$ediskNome] = [Environment]::GetEnvironmentVariable($ediskNome, 'Process')
}
Push-Location (Join-Path $ediskRaiz 'frontend')
try {
    $env:EDISK_DEV_ROOT = $ediskRaiz
    $env:EDISK_ENV = 'development'
    [Environment]::SetEnvironmentVariable('EDISK_TEST_ROOT', $null, 'Process')
    if ($BancoTemporario) {
        $env:EDISK_ENV = 'test'
        $env:EDISK_TEST_ROOT = Join-Path ([IO.Path]::GetTempPath()) ('edisk-integrado-' + [guid]::NewGuid().ToString('N'))
        Write-Host ('Dados descartaveis: ' + $env:EDISK_TEST_ROOT)
    }
    $ediskPython = Join-Path $ediskRaiz 'backend/.venv/Scripts/python.exe'
    if (!(Test-Path -LiteralPath $ediskPython -PathType Leaf)) {
        throw 'Ambiente backend/.venv ausente. Prepare as dependencias conforme README.'
    }
    $ediskFlag = '--' + $Modo.ToLowerInvariant()
    if ($Executavel) {
        if (!$SemBuild) {
            & flutter build windows $ediskFlag --no-pub --dart-define=EDISK_SOURCE_BACKEND=true
            if ($LASTEXITCODE -ne 0) { throw 'Build Windows falhou.' }
        }
        $ediskExe = Join-Path $ediskRaiz ('frontend/build/windows/x64/runner/' + $Modo + '/edisk.exe')
        if (!(Test-Path -LiteralPath $ediskExe -PathType Leaf)) { throw 'Executavel ainda nao compilado.' }
        # Janela visivel solicitada pelo desenvolvedor. Nao e helper residente.
        Start-Process -FilePath $ediskExe -WorkingDirectory ([IO.Path]::GetTempPath()) -Wait
    } else {
        & flutter run -d windows $ediskFlag --no-pub --dart-define=EDISK_SOURCE_BACKEND=true
        if ($LASTEXITCODE -ne 0) { throw 'Execucao Flutter encerrou com erro.' }
    }
} finally {
    Pop-Location
    foreach ($ediskNome in $ediskVariaveis) {
        [Environment]::SetEnvironmentVariable($ediskNome, $ediskAnterior[$ediskNome], 'Process')
    }
}
