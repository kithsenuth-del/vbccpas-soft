[Setup]
AppName=VBCCPAS Soft
AppVersion=1.0.0
DefaultDirName={commonpf64}\VBCCPAS Soft
DefaultGroupName=VBCCPAS Soft
OutputDir=.
OutputBaseFilename=vbccpas-soft-installer
Compression=lzma
SolidCompression=yes
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64

[Files]
Source: "..\dist\VBCCPAS-Desktop\VBCCPAS-Desktop.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\VBCCPAS-Web\VBCCPAS-Web.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\VBCCPAS Desktop"; Filename: "{app}\VBCCPAS-Desktop.exe"
Name: "{group}\VBCCPAS Web Server"; Filename: "{app}\VBCCPAS-Web.exe"

[Run]
Filename: "{app}\VBCCPAS-Desktop.exe"; Description: "Launch VBCCPAS Desktop"; Flags: nowait postinstall skipifsilent
