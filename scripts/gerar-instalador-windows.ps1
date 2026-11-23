param(
    [Parameter(Mandatory=$true)][string]$Bundle,
    [Parameter(Mandatory=$true)][string]$ManifestSha256,
    [Parameter(Mandatory=$true)][ValidateSet('tecnico','candidato')][string]$Canal,
    [Parameter(Mandatory=$true)][string]$Iscc,
    [Parameter(Mandatory=$true)][string]$Destino,
    [string]$InstanciaEnsaio,
    [string]$AprovacaoId,
    [string]$AprovacaoSha256
)
$ErrorActionPreference = 'Stop'
$ediskRaiz = Split-Path -Parent $PSScriptRoot
$ediskPython = Join-Path $ediskRaiz 'backend/.venv/Scripts/python.exe'
$ediskArgs = @('-B', (Join-Path $PSScriptRoot 'installer_windows.py'),
    '--bundle',$Bundle,'--manifest-sha256',$ManifestSha256,'--channel',$Canal,
    '--iscc',$Iscc,'--output',$Destino)
if ($InstanciaEnsaio) { $ediskArgs += @('--test-instance',$InstanciaEnsaio) }
if ($AprovacaoId) { $ediskArgs += @("--approval-id",$AprovacaoId,"--approval-sha256",$AprovacaoSha256) }
& $ediskPython @ediskArgs
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
