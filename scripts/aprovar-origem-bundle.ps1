param(
 [Parameter(Mandatory=$true)][string]$RegistroId,
 [Parameter(Mandatory=$true)][string]$RegistroSha256,
 [switch]$Confirmar
)
$ErrorActionPreference = 'Stop'
$ediskPython = Join-Path (Split-Path -Parent $PSScriptRoot) 'backend/.venv/Scripts/python.exe'
$ediskOrigin = Join-Path $PSScriptRoot 'build_origin.py'
& $ediskPython -B $ediskOrigin inspect --origin-id $RegistroId --origin-sha256 $RegistroSha256
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
if (!$Confirmar) { Write-Host 'Revise a origem e repita com -Confirmar para registrar sua aprovacao local.'; exit 0 }
& $ediskPython -B $ediskOrigin approve --origin-id $RegistroId --origin-sha256 $RegistroSha256
exit $LASTEXITCODE
