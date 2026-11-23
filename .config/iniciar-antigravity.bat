@echo off
setlocal EnableExtensions DisableDelayedExpansion

rem Este arquivo deve ficar em <projeto>\.config\iniciar-antigravity.bat
for %%I in ("%~dp0..") do set "PROJETO=%%~fI"
set "RTK_DIR=%PROJETO%\.tools\rtk\1.1.0"
set "RTK_EXE=%RTK_DIR%\rtk.exe"
set "RTK_CONFIG=%PROJETO%\.tools\rtk\config"
set "ANTIGRAVITY_EXE=%LOCALAPPDATA%\Programs\Antigravity IDE\Antigravity IDE.exe"
set "EXTENSIONS_DIR=%USERPROFILE%\.antigravity-ide\extensions"

if not exist "%PROJETO%\.config\" (
    echo [ERRO] Estrutura do projeto nao encontrada: "%PROJETO%"
    exit /b 1
)
if not exist "%RTK_EXE%" (
    echo [ERRO] RTK local nao encontrado: "%RTK_EXE%"
    exit /b 1
)
if not exist "%ANTIGRAVITY_EXE%" (
    echo [ERRO] Antigravity nao encontrado: "%ANTIGRAVITY_EXE%"
    exit /b 1
)

rem DIR /O:-N lista as versoes da extensao em ordem decrescente.
set "RG_DIR="
for /f "delims=" %%F in ('dir /b /ad /o-n "%EXTENSIONS_DIR%\openai.chatgpt-*" 2^>nul') do (
    if not defined RG_DIR if exist "%EXTENSIONS_DIR%\%%F\bin\windows-x86_64\rg.exe" (
        set "RG_DIR=%EXTENSIONS_DIR%\%%F\bin\windows-x86_64"
    )
)
if not defined RG_DIR (
    echo [ERRO] rg.exe da extensao Codex nao encontrado em:
    echo        "%EXTENSIONS_DIR%"
    exit /b 1
)

if not exist "%RTK_CONFIG%\" (
    echo [ERRO] Diretorio de configuracao RTK nao encontrado: "%RTK_CONFIG%"
    exit /b 1
)

set "CLAUDE_CONFIG_DIR=%RTK_CONFIG%"
set "PATH=%RTK_DIR%;%RG_DIR%;%PATH%"

pushd "%PROJETO%" || (
    echo [ERRO] Nao foi possivel acessar o projeto.
    exit /b 1
)
echo [OK] Projeto: "%PROJETO%"
echo [OK] RTK: "%RTK_EXE%"
echo [OK] Ripgrep: "%RG_DIR%\rg.exe"
echo [INFO] Abrindo Antigravity...
start "" "%ANTIGRAVITY_EXE%" "%PROJETO%"
set "RESULTADO=%ERRORLEVEL%"
popd
if not "%RESULTADO%"=="0" (
    echo [ERRO] Falha ao iniciar Antigravity. Codigo: %RESULTADO%
    exit /b %RESULTADO%
)
exit /b 0
