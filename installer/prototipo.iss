#ifndef PrototypeId
  #error PrototypeId obrigatorio
#endif
#ifndef PayloadVersion
  #error PayloadVersion obrigatorio
#endif
#ifndef PayloadFile
  #error PayloadFile obrigatorio
#endif
#ifndef WorkDir
  #error WorkDir obrigatorio
#endif
[Setup]
AppId=eDisk-Prototipo-{#PrototypeId}
AppName=Messaging Lab Prototipo {#PrototypeId}
AppVersion={#PayloadVersion}
DefaultDirName={localappdata}\Programs\eDisk-Prototipo-{#PrototypeId}
DefaultGroupName=Messaging Lab Prototipo {#PrototypeId}
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
OutputDir={#WorkDir}
OutputBaseFilename=prototipo-{#PayloadVersion}
Compression=lzma2
SolidCompression=yes
[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"
[Files]
Source: "{#PayloadFile}"; DestDir: "{app}\releases\{#PayloadVersion}"; DestName: "payload.txt"; Flags: ignoreversion
[Icons]
Name: "{userprograms}\Messaging Lab Prototipo {#PrototypeId}\Payload"; Filename: "{app}\releases\{#PayloadVersion}\payload.txt"
[Code]
function InitializeSetup(): Boolean;
begin
  Result := not IsAdminInstallMode;
  if Result then Log('EDISK_PROTOTYPE nonadmin=yes');
end;
