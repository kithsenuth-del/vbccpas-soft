#define MyAppName "VBCCPAS Soft"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Bandaranayake Central College Photographic Arts Society"

[Setup]
AppId={{9C16C9D5-7B32-4D45-9D7D-8A8D6E21BCCA}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\VBCCPAS Soft
DefaultGroupName=VBCCPAS Soft
DisableProgramGroupPage=yes
UsePreviousAppDir=yes
OutputDir=..\dist
OutputBaseFilename=VBCCPAS-Soft-Setup
SetupIconFile=..\assets\society-logo.ico
UninstallDisplayIcon={app}\VBCCPAS-Soft.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
CloseApplications=yes
RestartApplications=no
Uninstallable=yes

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
; Install the complete verified desktop package, but not its ZIP/checksum.
; The live society database and backup folders are not part of the package.
Source: "..\dist\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "*.zip,*.sha256,VBCCPAS-Soft-Setup.exe"

[Icons]
Name: "{group}\VBCCPAS Soft"; Filename: "{app}\VBCCPAS-Soft.exe"; WorkingDir: "{app}"
Name: "{group}\Uninstall VBCCPAS Soft"; Filename: "{uninstallexe}"
Name: "{autodesktop}\VBCCPAS Soft"; Filename: "{app}\VBCCPAS-Soft.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\VBCCPAS-Soft.exe"; Description: "Launch VBCCPAS Soft"; Flags: postinstall nowait skipifsilent
