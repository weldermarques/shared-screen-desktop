; Instalador do Shared Screen (Inno Setup 6).
; Gerado pelo build.ps1:  iscc /DAppVersion=1.2.3 installer.iss

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

#define AppName "Shared Screen"
#define AppExe "SharedScreen.exe"

[Setup]
AppId={{6B7E2C4A-3F1D-4E8B-9A57-2C8D1F0E4B21}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Welder Marques
AppPublisherURL=https://github.com/weldermarques/shared-screen-desktop
; Instala por usuário (sem UAC), o que permite o update silencioso.
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\SharedScreen
DisableProgramGroupPage=yes
DisableDirPage=auto
DisableReadyPage=yes
UsePreviousAppDir=yes
OutputDir=dist-installer
OutputBaseFilename=SharedScreen-Setup-{#AppVersion}
SetupIconFile=assets\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Fecha o app aberto antes de substituir os arquivos (update).
CloseApplications=force
RestartApplications=no

[Languages]
Name: "ptbr"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[InstallDelete]
; Remove bibliotecas da versão anterior para não misturar DLLs.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "dist\SharedScreen\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
; Sem "skipifsilent": no update silencioso o app reabre sozinho ao terminar.
Filename: "{app}\{#AppExe}"; Description: "Abrir o {#AppName}"; Flags: nowait postinstall
