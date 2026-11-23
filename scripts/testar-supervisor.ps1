param([switch]$SomenteCompilar, [string[]]$Testes)
$ErrorActionPreference = 'Stop'
$ediskRaiz = Split-Path -Parent $PSScriptRoot
$ediskVswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio/Installer/vswhere.exe'
$ediskVs = & $ediskVswhere -latest -property installationPath
$ediskCmake = Join-Path $ediskVs 'Common7/IDE/CommonExtensions/Microsoft/CMake/CMake/bin/cmake.exe'
$ediskBuild = Join-Path $ediskRaiz 'frontend/build/supervisor-tests'
& $ediskCmake -S (Join-Path $ediskRaiz 'frontend/windows/runner/tests') -B $ediskBuild -A x64
if ($LASTEXITCODE -ne 0) { throw 'Falha ao configurar harness nativo.' }
& $ediskCmake --build $ediskBuild --config Debug
if ($LASTEXITCODE -ne 0) { throw 'Falha ao compilar harness nativo.' }
if (!$SomenteCompilar) {
    & (Join-Path $ediskRaiz 'backend/.venv/Scripts/python.exe') -B (Join-Path $PSScriptRoot 'tests/test_supervisor.py') @Testes
    if ($LASTEXITCODE -ne 0) { throw 'Falha nos ensaios do supervisor.' }
}
