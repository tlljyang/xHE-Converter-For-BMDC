; xHE4BMDC Onekey installer (Inno Setup)
; Build:  ISCC.exe installer.iss  (from packaging/windows, with dist-app + deps staged)
; Expects next to this file:
;   stage/app/*            frozen app (PyInstaller COLLECT output)
;   stage/ffmpeg/bin/*     ffmpeg.exe + ffprobe.exe
;   stage/bin/*            xaacdec.exe

#define AppName "xHE Converter for BMDC"
#define AppVersion "0.1.0"
#define AppPublisher "tlljyang"
#define AppExeName "xHE4BMDC.exe"

[Setup]
AppId={{8E3B6C1A-52D7-4B9E-9F2A-XHE4BMDC01}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={localappdata}\Programs\xHE4BMDC
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=.
OutputBaseFilename=xHE4BMDC-Onekey-Setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\app\{#AppExeName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "quicklaunchicon"; Description: "{cm:CreateQuickLaunchIcon}"; GroupDescription: "{cm:AdditionalIcons}"; OnlyBelowVersion: 6.1; Check: not IsAdminInstallMode

[Files]
Source: "stage\app\*"; DestDir: "{app}\app"; Flags: ignoreversion recursesubdirs
Source: "stage\ffmpeg\bin\*"; DestDir: "{app}\app\ffmpeg\bin"; Flags: ignoreversion
Source: "stage\bin\*"; DestDir: "{app}\app\bin"; Flags: ignoreversion
Source: "..\..\LICENSE"; DestDir: "{app}"; DestName: "LICENSE"
Source: "..\..\THIRD_PARTY_NOTICES.md"; DestDir: "{app}"; Flags: skipifsourcedoesntexist

[Icons]
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\app\{#AppExeName}"; Tasks: desktopicon
Name: "{group}\{#AppName}"; Filename: "{app}\app\{#AppExeName}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\app\{#AppExeName}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; keep user data (tasks db, logs, presets) under %LOCALAPPDATA% by default — nothing to clean here
Type: filesandordirs; Name: "{app}\app\.xhe4bmdc-work"
