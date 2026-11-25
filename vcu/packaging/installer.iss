; WS22 VCU installer (Inno Setup 6). Built by packaging/build_windows.bat or CI.
; Per-user install (no admin rights). Configs + logs live in Documents\WS22_VCU.
#define AppVer GetEnv("VCU_VERSION")
#if AppVer == ""
  #define AppVer "1.0.0"
#endif

[Setup]
AppId={{6B1E3F0A-5C2D-4E8B-9A41-2C7D5E0F8A12}
AppName=WS22 VCU
AppVersion={#AppVer}
AppPublisher=KUST WSC27 Electronic Team
DefaultDirName={localappdata}\Programs\WS22_VCU
DefaultGroupName=WS22 VCU
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=WS22_VCU_Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\WS22_VCU.exe
DisableProgramGroupPage=yes

[Languages]
#if FileExists(AddBackslash(CompilerPath) + "Languages\Korean.isl")
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"
#endif
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Dirs]
Name: "{userdocs}\WS22_VCU"; Flags: uninsneveruninstall

[Files]
Source: "..\dist\WS22_VCU.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\WS22 VCU"; Filename: "{app}\WS22_VCU.exe"
Name: "{group}\WS22 VCU 설정·로그 폴더"; Filename: "{userdocs}\WS22_VCU"
Name: "{group}\{cm:UninstallProgram,WS22 VCU}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\WS22 VCU"; Filename: "{app}\WS22_VCU.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\WS22_VCU.exe"; Description: "{cm:LaunchProgram,WS22 VCU}"; Flags: nowait postinstall skipifsilent
