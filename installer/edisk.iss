#ifndef Stage
  #error Stage obrigatorio
#endif
#include Stage + "/config.iss"
[Setup]
AppId={#AppId}
AppName=Messaging Lab ({#Channel} - demonstracao experimental)
AppVersion={#AppVersion}
VersionInfoVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\{#InstallFolder}
DefaultGroupName=Messaging Lab ({#Channel})
PrivilegesRequired=lowest
ArchitecturesAllowed=x64os
ArchitecturesInstallIn64BitMode=x64os
MinVersion=10.0
DisableDirPage=yes
DisableProgramGroupPage=yes
UsePreviousAppDir=no
UninstallLogMode=append
CloseApplications=no
RestartApplications=no
Compression=lzma2
SolidCompression=yes
UninstallDisplayIcon={app}\edisk-launcher.exe
SetupLogging=yes
InfoBeforeFile=AVISOS_INSTALACAO.txt
[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"
[Tasks]
Name: "desktopicon"; Description: "Criar atalho na area de trabalho"; Flags: unchecked
[Files]
#include Stage + "/files.iss"
[Icons]
Name: "{userprograms}\{#InstallFolder}\Messaging Lab"; Filename: "{app}\edisk-launcher.exe"
Name: "{userprograms}\{#InstallFolder}\Avisos de terceiros"; Filename: "{app}\releases\{#ReleaseId}\THIRD_PARTY_NOTICES.md"
Name: "{userdesktop}\{#InstallFolder}"; Filename: "{app}\edisk-launcher.exe"; Tasks: desktopicon
[UninstallDelete]
Type: files; Name: "{app}\active.txt"
Type: files; Name: "{app}\active.next"
Type: files; Name: "{app}\pending.txt"
Type: files; Name: "{app}\maintenance.lock"
#ifdef TestBuild
Type: files; Name: "{app}\test-pause.txt"
#endif
[Code]
var
  Maintenance: THandle;
  Held, ActivationPrepared, Completed, Aborted: Boolean;

function OpenLock(FileName: String; Access, Share: LongWord; Security: NativeUInt;
  Creation, Flags: LongWord; Template: THandle): THandle;
  external 'CreateFileW@kernel32.dll stdcall';
function CloseLock(Handle: THandle): Boolean;
  external 'CloseHandle@kernel32.dll stdcall';
function Attributes(FileName: String): LongWord;
  external 'GetFileAttributesW@kernel32.dll stdcall';
function PublishFile(Existing, NewName: String; Flags: LongWord): Boolean;
  external 'MoveFileExW@kernel32.dll stdcall';

procedure CheckPath(Path: String);
var Parent: String; Attr: LongWord;
begin
  while Path <> '' do begin
    Attr := Attributes(Path);
    if (Attr <> $FFFFFFFF) and ((Attr and $400) <> 0) then
      RaiseException('Links e reparse points nao sao permitidos na instalacao.');
    Parent := ExtractFileDir(Path);
    if Parent = Path then Break;
    Path := Parent;
  end;
end;

function CountPayload(Path: String): Integer;
var Item: TFindRec;
begin
  Result := 0;
  CheckPath(Path);
  if FindFirst(Path + '\*', Item) then begin
    try
      repeat
        if (Item.Name <> '.') and (Item.Name <> '..') then begin
          if (Item.Attributes and $400) <> 0 then RaiseException('Reparse point no payload.');
          if (Item.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0 then
            Result := Result + CountPayload(Path + '\' + Item.Name)
          else Result := Result + 1;
        end;
      until not FindNext(Item);
    finally
      FindClose(Item);
    end;
  end;
end;

#include Stage + "/checks.iss"

procedure WriteChecked(Name, Content: String);
begin
  CheckPath(Name);
  if not SaveStringToFile(Name, Content, False) then RaiseException('Nao foi possivel persistir o estado de instalacao.');
end;

function LastError(): LongWord;
  external 'GetLastError@kernel32.dll stdcall';
type
  TProcessTime = record
    Low, High: LongWord;
  end;
function OpenProcessHandle(Access: LongWord; Inherit: Boolean; Pid: LongWord): THandle;
  external 'OpenProcess@kernel32.dll stdcall';
function ProcessTimes(Handle: THandle; var Created, Exited, Kernel, User: TProcessTime): Boolean;
  external 'GetProcessTimes@kernel32.dll stdcall';
function ProcessWait(Handle: THandle; Milliseconds: LongWord): LongWord;
  external 'WaitForSingleObject@kernel32.dll stdcall';

procedure CheckRetired(FileName: String);
var Lines: TArrayOfString; Pid: Integer; Low, High: Int64; Payload: AnsiString;
    Handle: THandle; Created, Exited, Kernel, User: TProcessTime;
begin
  CheckPath(FileName);
  if not LoadStringsFromFile(FileName, Lines) then RaiseException('Estado de processos ilegivel.');
  if GetArrayLength(Lines) <> 5 then RaiseException('Encerramento de processos nao confirmado.');
  if Lines[0] <> 'EDISK-CONFIRMED-3' then RaiseException('Encerramento de processos nao confirmado.');
  Payload := Lines[0] + #13#10 + Lines[1] + #13#10 + Lines[2] + #13#10 + Lines[3] + #13#10;
  if GetSHA256OfString(Payload) <> Lines[4] then RaiseException('Confirmacao de processos corrompida.');
  Pid := StrToIntDef(Lines[1], -1);
  Low := StrToInt64Def(Lines[2], -1);
  High := StrToInt64Def(Lines[3], -1);
  if (Pid <= 0) or (Low < 0) or (Low > 4294967295) or (High <= 0) or (High > 4294967295) then
    RaiseException('Identidade do coordenador invalida.');
  Handle := OpenProcessHandle($101000, False, Pid);
  if Handle = 0 then begin
    { PID nao zero deixou de ser valido. Erros de acesso nunca significam termino. }
    if LastError() <> 87 then RaiseException('Nao foi possivel comprovar termino do coordenador.');
    Exit;
  end;
  try
    if not ProcessTimes(Handle, Created, Exited, Kernel, User) then
      RaiseException('Identidade do coordenador indisponivel.');
    if (Created.Low <> Low) or (Created.High <> High) then
      RaiseException('PID reutilizado ou identidade divergente; requer suporte.');
    if ProcessWait(Handle, 0) <> 0 then RaiseException('Coordenador ainda em encerramento.');
  finally CloseLock(Handle); end;
end;

procedure RequireConfirmed();
var Item: TFindRec; Path, FileName: String; Attr: LongWord; Retired: TArrayOfString; I: Integer;
begin
  Path := ExpandConstant('{app}\runtime');
  CheckPath(Path);
  Attr := Attributes(Path);
  if Attr = $FFFFFFFF then begin
    if (LastError() <> 2) and (LastError() <> 3) then RaiseException('Estado de processos ilegivel.');
    Exit;
  end;
  if (Attr and FILE_ATTRIBUTE_DIRECTORY) = 0 then RaiseException('Estado de processos invalido.');
  if not FindFirst(Path + '\*', Item) then RaiseException('Confirmacao de encerramento indisponivel.');
  try
    repeat
      if (Item.Name <> '.') and (Item.Name <> '..') then begin
        if ((Item.Attributes and ($400 or FILE_ATTRIBUTE_DIRECTORY)) <> 0) or
          (Length(Item.Name) <> 38) or (ExtractFileExt(Item.Name) <> '.claim') then
          RaiseException('Estado de processos desconhecido; manutencao bloqueada.');
        for I := 1 to 32 do
          if Pos(Copy(Item.Name, I, 1), '0123456789abcdef') = 0 then RaiseException('Identidade de estado invalida.');
        FileName := Path + '\' + Item.Name;
        CheckRetired(FileName);
        SetArrayLength(Retired, GetArrayLength(Retired) + 1);
        Retired[GetArrayLength(Retired) - 1] := FileName;
      end;
    until not FindNext(Item);
  finally FindClose(Item); end;
  { Somente depois de validar todos os estados e sob exclusividade. }
  for I := 0 to GetArrayLength(Retired) - 1 do
    if not DeleteFile(Retired[I]) then RaiseException('Nao foi possivel retirar confirmacao concluida.');
end;

function Acquire(): Boolean;
begin
  Result := False;
  CheckPath(ExpandConstant('{app}'));
  if not ForceDirectories(ExpandConstant('{app}')) then Exit;
  CheckPath(ExpandConstant('{app}\maintenance.lock'));
  { Share delete permite remover o arquivo somente ao desinstalar; leitores nao compartilham escrita. }
  Maintenance := OpenLock(ExpandConstant('{app}\maintenance.lock'), $C0000000, 4, 0, 4, $80, 0);
  Held := Maintenance <> THandle(-1);
  if Held then RequireConfirmed();
  Result := Held;
end;

procedure ValidateIdentity();
var Lines: TArrayOfString;
begin
  CheckPath(ExpandConstant('{app}\installation.id'));
  if not LoadStringsFromFile(ExpandConstant('{app}\installation.id'), Lines) then
    RaiseException('Identidade da instalacao ausente. Nao alterar diretorio desconhecido.');
  if (GetArrayLength(Lines) <> 2) then RaiseException('Identidade invalida.');
  if (Lines[0] <> 'EDISK-INSTALL-3') or (Lines[1] <> '{#Channel}') then
    RaiseException('Instalacao pertence a outro canal ou produto.');
end;

procedure Fault(Point: String);
begin
#ifdef TestBuild
  if ExpandConstant('{param:EDISKPAUSE|}') = Point then begin
    WriteChecked(ExpandConstant('{app}\test-pause.txt'), Point);
    while True do Sleep(100);
  end;
  if ExpandConstant('{param:EDISKFAIL|}') = Point then RaiseException('Falha injetada no ensaio: ' + Point);
#endif
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var Existing: Boolean; Item: TFindRec; Lines: TArrayOfString;
    OldVersion, NewVersion: Int64;
begin
  Result := '';
  try
    if CompareText(ExpandConstant('{app}'), ExpandConstant('{localappdata}\Programs\{#InstallFolder}')) <> 0 then
      RaiseException('O diretorio de instalacao por usuario e fixo.');
    Existing := DirExists(ExpandConstant('{app}'));
    if not Acquire() then begin
      Result := 'Feche todas as instancias do Messaging Lab e tente novamente. A manutencao exige exclusividade.';
      Exit;
    end;
    if FileExists(ExpandConstant('{app}\installation.id')) then begin
      ValidateIdentity();
      CheckPath(ExpandConstant('{app}\maximum-version.txt'));
      if not LoadStringsFromFile(ExpandConstant('{app}\maximum-version.txt'), Lines) then
        RaiseException('Historico de versao ausente. Reparacao requer suporte.');
      if GetArrayLength(Lines) <> 1 then RaiseException('Historico de versao invalido.');
      if not StrToVersion(Lines[0], OldVersion) or not StrToVersion('{#AppVersion}', NewVersion) then
        RaiseException('Versao de instalacao invalida.');
      if ComparePackedVersion(OldVersion, NewVersion) > 0 then RaiseException('Downgrade recusado. Instale a mesma versao ou uma posterior.');
      if FileExists(ExpandConstant('{app}\active.txt')) then begin
        CheckPath(ExpandConstant('{app}\active.txt'));
        if not LoadStringsFromFile(ExpandConstant('{app}\active.txt'), Lines) then RaiseException('Estado ativo ilegivel.');
        if GetArrayLength(Lines) = 5 then begin
          if (Lines[0] = 'EDISK-ACTIVE-1') and (Lines[1] = '{#Channel}') and
            (Lines[2] = '{#AppVersion}') and (Lines[3] <> '{#ReleaseId}') then
            RaiseException('Mesmo numero de versao com outro payload. Incremente o build.');
        end else Log('EDISK reparando referencia ativa corrompida; limite de versao preservado.');
      end;
    end else if Existing then begin
      if FindFirst(ExpandConstant('{app}\*'), Item) then begin
        try
          repeat
            if (Item.Name <> '.') and (Item.Name <> '..') and (Item.Name <> 'maintenance.lock') then
              RaiseException('Diretorio existente sem identidade reconhecida.');
          until not FindNext(Item);
        finally FindClose(Item); end;
      end;
    end;
#ifdef TestBuild
    DeleteFile(ExpandConstant('{app}\test-pause.txt'));
#endif
    CheckPath(ExpandConstant('{app}\releases\{#ReleaseId}'));
    WriteChecked(ExpandConstant('{app}\pending.txt'), 'EDISK-PENDING-1'#13#10'{#ReleaseId}'#13#10);
    WriteChecked(ExpandConstant('{app}\installation.id'), 'EDISK-INSTALL-3'#13#10'{#Channel}'#13#10);
    WriteChecked(ExpandConstant('{app}\maximum-version.txt'), '{#AppVersion}'#13#10);
  except
    Result := GetExceptionMessage;
    if Held then begin CloseLock(Maintenance); Held := False; end;
  end;
end;

procedure CurStepChanged(Step: TSetupStep);
begin
  if Aborted then Exit;
  try
  if Step = ssInstall then Fault('copy');
  if Step = ssPostInstall then begin
    CountPayload(ExpandConstant('{app}\releases\{#ReleaseId}'));
    VerifyPayload();
    Fault('before-activation');
    WriteChecked(ExpandConstant('{app}\active.next'),
      'EDISK-ACTIVE-1'#13#10'{#Channel}'#13#10'{#AppVersion}'#13#10'{#ReleaseId}'#13#10'{#InventoryHash}'#13#10);
    if not PublishFile(ExpandConstant('{app}\active.next'), ExpandConstant('{app}\active.txt'), 9) then
      RaiseException('Nao foi possivel publicar a versao ativa. Execute novamente o instalador.');
    Fault('after-activation');
    ActivationPrepared := True;
  end;
  if Step = ssDone then begin
    if not ActivationPrepared then begin
      Log('EDISK finalizacao bloqueada; ativacao nao verificada.');
      Exit;
    end;
    Fault('finalize');
    VerifyPayload();
    if not DeleteFile(ExpandConstant('{app}\pending.txt')) then
      RaiseException('Finalizacao pendente. Execute novamente o instalador.');
    Completed := True;
  end;
  except
    Aborted := True;
    RaiseException(GetExceptionMessage);
  end;
end;

function GetCustomSetupExitCode(): Integer;
begin
  if Completed then Result := 0 else Result := 37;
end;

procedure DeinitializeSetup();
begin
  if Held then begin CloseLock(Maintenance); Held := False; end;
end;

function InitializeUninstall(): Boolean;
begin
  Result := False;
  try
    if not Acquire() then begin
    SuppressibleMsgBox('Feche todas as instancias do Messaging Lab antes de desinstalar.', mbError, MB_OK, IDOK);
      Exit;
    end;
    ValidateIdentity();
    CountPayload(ExpandConstant('{app}\releases'));
    WriteChecked(ExpandConstant('{app}\pending.txt'), 'EDISK-UNINSTALL-1'#13#10);
    Result := True;
  except
    SuppressibleMsgBox(GetExceptionMessage, mbError, MB_OK, IDOK);
  end;
end;

procedure DeinitializeUninstall();
begin
  if Held then begin CloseLock(Maintenance); Held := False; end;
end;
