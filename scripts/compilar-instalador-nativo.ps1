param([switch]$SomenteCompilar)
$ErrorActionPreference = 'Stop'
$ediskRaiz = Split-Path -Parent $PSScriptRoot
$ediskVswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio/Installer/vswhere.exe'
$ediskVs = & $ediskVswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (!$ediskVs) { throw 'Visual Studio C++ x64 ausente.' }
$ediskCmake = Join-Path $ediskVs 'Common7/IDE/CommonExtensions/Microsoft/CMake/CMake/bin/cmake.exe'
$ediskBuild = Join-Path $ediskRaiz 'build/installer-native'
& $ediskCmake -S (Join-Path $ediskRaiz 'installer/native') -B $ediskBuild -A x64
if ($LASTEXITCODE -ne 0) { throw 'Falha ao configurar launcher.' }
& $ediskCmake --build $ediskBuild --config Release
if ($LASTEXITCODE -ne 0) { throw 'Falha ao compilar launcher.' }
