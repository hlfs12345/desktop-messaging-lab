param([ValidateSet('tecnico','candidato')][string]$Canal = 'tecnico')
# Gera artefato tecnico ou candidato ainda nao aprovado comercialmente. Nao instala ferramentas ou altera politicas Windows.
$ErrorActionPreference = 'Stop'
$ediskRaiz = Split-Path -Parent $PSScriptRoot
$ediskId = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ') + '-' + [guid]::NewGuid().ToString('N').Substring(0,8)
$ediskWork = Join-Path $ediskRaiz ('build/packaging/' + $ediskId)
$ediskBundle = Join-Path $ediskWork 'eDisk'
$ediskPython = Join-Path $ediskRaiz 'backend/.venv/Scripts/python.exe'
$ediskOriginalEpoch = $env:SOURCE_DATE_EPOCH
$ediskChecks = [System.Collections.Generic.List[object]]::new()
$ediskInfo = [ordered]@{
    artifact = 'validacao-tecnica-nao-comercial'; id = $ediskId
    status = 'preparing'; windows_clean = 'pendente'; commercial_approval = $false
    default_profile = 'bundled/test'; production_requires = 'indisponivel-no-perfil-tecnico'
    migration_head = $null; checks = $ediskChecks
    python = '3.14.10'; pyinstaller = '6.22.3'; hooks = '2026.9'
    limitations = @('Windows limpo pendente', 'Revisao juridica final pendente', 'Sem assinatura digital')
}
function Invoke-EdiskCheck([string]$Nome, [string]$Comando, [string[]]$Argumentos) {
    $ediskWatch = [Diagnostics.Stopwatch]::StartNew()
    $ediskExit = -1
    try {
        & $Comando @Argumentos
        $ediskExit = $LASTEXITCODE
        if ($ediskExit -ne 0) { throw ('Etapa falhou: ' + $Nome + ' (codigo ' + $ediskExit + ')') }
    } finally {
        $ediskChecks.Add([ordered]@{name=$Nome; exit_code=$ediskExit; seconds=[math]::Round($ediskWatch.Elapsed.TotalSeconds,3)})
    }
}
Push-Location $ediskRaiz
try {
    New-Item -ItemType Directory -Path $ediskWork -Force | Out-Null
    $ediskInfo['commit'] = (& git rev-parse HEAD).Trim()
    if ($LASTEXITCODE -ne 0) { throw 'Git de origem indisponivel.' }
    $ediskInfo['worktree_dirty'] = [bool](& git status --porcelain)
    $ediskInfo['channel'] = $Canal
    if ($Canal -eq 'candidato' -and $ediskInfo.worktree_dirty) { throw 'Candidato exige Git limpo antes do build.' }
    $ediskOriginId = (& $ediskPython -B (Join-Path $PSScriptRoot 'build_origin.py') begin --channel $Canal).Trim()
    if ($LASTEXITCODE -ne 0) { throw 'Falha ao iniciar origem confiavel.' }
    $ediskInfo['origin_id'] = $ediskOriginId
    $ediskProduto = (& $ediskPython -B (Join-Path $PSScriptRoot 'release_metadata.py') json | ConvertFrom-Json)
    if ($LASTEXITCODE -ne 0) { throw 'Versao de produto inconsistente.' }
    $ediskInfo['release_schema'] = 1
    $ediskInfo['installation_protocol'] = 3
    $ediskInfo['app_version'] = $ediskProduto.native_version
    $ediskInfo['channel'] = $Canal
    $ediskInfo['architecture'] = 'x64'
    if ($Canal -eq 'candidato') {
        $ediskInfo.default_profile = 'bundled/production'
        $ediskInfo.production_requires = 'politica-incorporada-no-runner'
        $ediskInfo['candidate_gui'] = 'pendente-em-perfil-Windows-isolado'
    }
    Invoke-EdiskCheck 'native-launcher-build' 'powershell' @('-NoProfile','-ExecutionPolicy','Bypass','-File',(Join-Path $PSScriptRoot 'compilar-instalador-nativo.ps1'))
    if (!(Test-Path -LiteralPath $ediskPython)) { throw 'Prepare backend/.venv conforme README.' }
    $ediskInfo.migration_head = (& $ediskPython -B -c 'from app.runtime.bundle_integrity import HEAD; print(HEAD)').Trim()
    if ($LASTEXITCODE -ne 0) { throw 'Revisao de migracoes indisponivel.' }
    Invoke-EdiskCheck 'native-launcher-startup' $ediskPython @('-B',(Join-Path $PSScriptRoot 'tests/test_installer_native.py'))
    $ediskFlutterCommand = (Get-Command flutter -ErrorAction Stop).Source
    $ediskFlutter = (& flutter --version --machine | ConvertFrom-Json)
    if ($LASTEXITCODE -ne 0 -or $ediskFlutter.frameworkVersion -ne '3.47.8') { throw 'Flutter 3.47.8 obrigatorio.' }
    $ediskSdk = Split-Path -Parent (Split-Path -Parent $ediskFlutterCommand)
    $ediskVswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio/Installer/vswhere.exe'
    $ediskVs = & $ediskVswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
    if (!$ediskVs) { throw 'Visual Studio C++ x64 ausente.' }
    $ediskCrtVersion = (Get-Content -LiteralPath (Join-Path $ediskVs 'VC/Auxiliary/Build/Microsoft.VCRedistVersion.default.txt')).Trim()
    $ediskCrt = Get-ChildItem -LiteralPath (Join-Path $ediskVs ('VC/Redist/MSVC/' + $ediskCrtVersion + '/x64')) -Directory -Filter '*.CRT' | Select-Object -First 1
    if (!$ediskCrt) { throw 'CRT redistribuivel release x64 ausente.' }
    $ediskInfo['flutter'] = $ediskFlutter.frameworkVersion
    $ediskInfo['engine'] = $ediskFlutter.engineRevision
    $ediskInfo['crt'] = $ediskCrtVersion
    $ediskInfo['commit'] = (& git rev-parse HEAD).Trim()
    $env:SOURCE_DATE_EPOCH = (& git show -s --format=%ct HEAD).Trim()
    $ediskInfo['source_date_epoch'] = $env:SOURCE_DATE_EPOCH
    $ediskInfo['worktree_dirty'] = [bool](& git status --porcelain)
    $ediskInfo['uv_lock_sha256'] = (Get-FileHash (Join-Path $ediskRaiz 'backend/uv.lock') -Algorithm SHA256).Hash
    $ediskInfo['pub_lock_sha256'] = (Get-FileHash (Join-Path $ediskRaiz 'frontend/pubspec.lock') -Algorithm SHA256).Hash
    Invoke-EdiskCheck 'toolchain-python' $ediskPython @('-B',(Join-Path $PSScriptRoot 'bundle_windows.py'),'preflight')
    Push-Location (Join-Path $ediskRaiz 'backend')
    try {
        Invoke-EdiskCheck 'lock' 'uv' @('lock','--check')
        Invoke-EdiskCheck 'dependencies' 'uv' @('pip','check')
        Invoke-EdiskCheck 'ruff' 'uv' @('run','--locked','--no-sync','ruff','check','.','../scripts')
        Invoke-EdiskCheck 'python-format' 'uv' @('run','--locked','--no-sync','ruff','format','--check','.','../scripts')
        Invoke-EdiskCheck 'pytest' 'uv' @('run','--locked','--no-sync','pytest','-q','--tb=short')
    } finally { Pop-Location }
    Push-Location (Join-Path $ediskRaiz 'frontend')
    try {
        Invoke-EdiskCheck 'pub-lock-offline' 'flutter' @('pub','get','--offline','--enforce-lockfile')
        Invoke-EdiskCheck 'flutter-analyze' 'flutter' @('analyze','--no-pub')
        Invoke-EdiskCheck 'flutter-test' 'flutter' @('test','--no-pub','--reporter','compact')
        Invoke-EdiskCheck 'dart-format' 'dart' @('format','--output=none','--set-exit-if-changed','lib','test')
        Invoke-EdiskCheck 'flutter-debug' 'flutter' @('build','windows','--debug','--no-pub','--dart-define=EDISK_SOURCE_BACKEND=true')
        Invoke-EdiskCheck 'flutter-release-source' 'flutter' @('build','windows','--release','--no-pub','--dart-define=EDISK_SOURCE_BACKEND=true')
    } finally { Pop-Location }
    Invoke-EdiskCheck 'native-supervisor' 'powershell' @('-NoProfile','-ExecutionPolicy','Bypass','-File',(Join-Path $PSScriptRoot 'testar-supervisor.ps1'))
    Invoke-EdiskCheck 'native-resolver' $ediskPython @('-B',(Join-Path $PSScriptRoot 'tests/test_resolver.py'))
    Invoke-EdiskCheck 'windows-regression' $ediskPython @('-B',(Join-Path $PSScriptRoot 'tests/test_windows_app.py'))

    Push-Location (Join-Path $ediskRaiz 'backend')
    try {
        Invoke-EdiskCheck 'pyinstaller' 'uv' @('run','--locked','--no-sync','pyinstaller','--noconfirm','--clean','--distpath',(Join-Path $ediskWork 'backend-dist'),'--workpath',(Join-Path $ediskWork 'backend-work'),'packaging/edisk-backend.spec')
        Invoke-EdiskCheck 'pyinstaller-repeat' 'uv' @('run','--locked','--no-sync','pyinstaller','--noconfirm','--clean','--distpath',(Join-Path $ediskWork 'repeat-dist'),'--workpath',(Join-Path $ediskWork 'repeat-work'),'packaging/edisk-backend.spec')
    } finally { Pop-Location }
    $ediskFirst = Join-Path $ediskWork 'backend-dist/edisk-backend'
    $ediskRepeat = Join-Path $ediskWork 'repeat-dist/edisk-backend'
    $ediskDifference = @()
    foreach ($ediskFile in Get-ChildItem -LiteralPath $ediskFirst -File -Recurse) {
        $ediskRelative = $ediskFile.FullName.Substring($ediskFirst.Length + 1)
        $ediskOther = Join-Path $ediskRepeat $ediskRelative
        if (!(Test-Path -LiteralPath $ediskOther)) { throw 'Build repetido tem conteudo diferente.' }
        if ((Get-FileHash $ediskFile.FullName).Hash -ne (Get-FileHash $ediskOther).Hash) { $ediskDifference += $ediskRelative }
    }
    if (@(Get-ChildItem -LiteralPath $ediskFirst -File -Recurse).Count -ne @(Get-ChildItem -LiteralPath $ediskRepeat -File -Recurse).Count) { throw 'Build repetido tem arquivos adicionais.' }
    $ediskInfo['repeat_backend_different_files'] = $ediskDifference
    if ($ediskDifference.Count -ne 0) { throw 'Backend repetido divergiu; nao promover.' }
    Push-Location (Join-Path $ediskRaiz 'frontend')
    try {
        $ediskReleaseArgs = @('build','windows','--release','--no-pub')
        if ($Canal -eq 'candidato') { $ediskReleaseArgs += '--dart-define=EDISK_DISTRIBUTION=true' }
        Invoke-EdiskCheck 'flutter-release-bundle' 'flutter' $ediskReleaseArgs
    } finally { Pop-Location }
    Invoke-EdiskCheck 'stage' $ediskPython @('-B',(Join-Path $PSScriptRoot 'bundle_windows.py'),'stage',$ediskBundle,'--flutter',(Join-Path $ediskRaiz 'frontend/build/windows/x64/runner/Release'),'--backend',$ediskFirst,'--crt',$ediskCrt.FullName,'--sdk',$ediskSdk,'--vs',$ediskVs)
    Copy-Item -LiteralPath (Join-Path $ediskRaiz 'build/installer-native/Release/edisk-launcher.exe') -Destination (Join-Path $ediskBundle 'edisk-launcher.exe')
    $ediskInfo | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $ediskBundle 'build-info.json') -Encoding UTF8
    Invoke-EdiskCheck 'content' $ediskPython @('-B',(Join-Path $PSScriptRoot 'bundle_windows.py'),'validate',$ediskBundle)
    Invoke-EdiskCheck 'packaged-backend' $ediskPython @('-B',(Join-Path $PSScriptRoot 'tests/test_backend_bundle.py'),(Join-Path $ediskBundle 'backend/edisk-backend.exe'))
    if ($Canal -eq 'tecnico') {
        Invoke-EdiskCheck 'packaged-windows' $ediskPython @('-B',(Join-Path $PSScriptRoot 'tests/test_windows_bundle.py'),$ediskBundle)
    } else {
        # Nao executar producao no perfil do desenvolvedor. Este check e somente politica compilada.
        Invoke-EdiskCheck 'packaged-windows' $ediskPython @('-B',(Join-Path $PSScriptRoot 'tests/test_candidate_policy.py'))
    }
    $ediskInfo.status = 'technical-checks-passed-clean-windows-pending'
    $ediskInfo | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $ediskBundle 'build-info.json') -Encoding UTF8
    Invoke-EdiskCheck 'inventory' $ediskPython @('-B',(Join-Path $PSScriptRoot 'installer_inventory.py'),$ediskBundle,$Canal)
    Invoke-EdiskCheck 'manifest' $ediskPython @('-B',(Join-Path $PSScriptRoot 'bundle_windows.py'),'manifest',$ediskBundle)
    if ($Canal -eq 'candidato' -and ([bool](& git status --porcelain) -or ((& git rev-parse HEAD).Trim() -ne $ediskInfo.commit))) { throw 'Origem Git alterada durante o build candidato; nao promover.' }
    $ediskOriginHash = (& $ediskPython -B (Join-Path $PSScriptRoot 'build_origin.py') finish --origin-id $ediskOriginId --bundle $ediskBundle).Trim()
    if ($LASTEXITCODE -ne 0) { throw 'Falha ao fechar origem confiavel.' }
    [ordered]@{origin_id=$ediskOriginId; origin_record_sha256=$ediskOriginHash} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $ediskWork 'origin-reference.json') -Encoding UTF8
    Write-Host ('Registro de origem: ' + $ediskOriginId + ' SHA256 ' + $ediskOriginHash)
    $ediskDestination = Join-Path $ediskRaiz ('dist/eDisk-' + $Canal + '-' + $ediskId)
    if (Test-Path -LiteralPath $ediskDestination) { throw 'Destino ja existe; nao sobrescrever.' }
    New-Item -ItemType Directory -Path (Split-Path -Parent $ediskDestination) -Force | Out-Null
    # Ambos os caminhos sao absolutos, derivados da raiz, sem entrada externa.
    if (!(($ediskBundle | Resolve-Path).Path.StartsWith((Join-Path $ediskRaiz 'build/packaging'),[StringComparison]::OrdinalIgnoreCase))) { throw 'Staging fora da raiz esperada.' }
    Move-Item -LiteralPath $ediskBundle -Destination $ediskDestination
    Write-Host ('Bundle tecnico: ' + $ediskDestination)
} catch {
    $ediskInfo.status = 'failed-no-promotion'
    $ediskInfo['failure'] = 'Falha impeditiva; consultar etapas e saida do comando. Nao aprovado.'
    $ediskInfo['failure_reason'] = $_.Exception.Message
    if (Test-Path -LiteralPath $ediskBundle) {
        $ediskInfo | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $ediskBundle 'build-info.json') -Encoding UTF8
        & $ediskPython -B (Join-Path $PSScriptRoot 'bundle_windows.py') manifest $ediskBundle
    }
    $ediskInfo | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $ediskWork 'build-failure.json') -Encoding UTF8
    Write-Error ('Empacotamento interrompido sem promocao. Staging: ' + $ediskWork + '. ' + $_.Exception.Message) -ErrorAction Continue
    exit 1
} finally {
    $env:SOURCE_DATE_EPOCH = $ediskOriginalEpoch
    Pop-Location
}
