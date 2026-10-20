#define AppName "Photo Studio"
#define AppVer "7.2.0"
[Setup]
AppId={{69B4E6F0-67DB-4D14-914B-057B1E8408BE}
AppName={#AppName}
AppVersion={#AppVer}
DefaultDirName={autopf}\Photo Studio
DefaultGroupName=Photo Studio
OutputDir=..\artifacts
OutputBaseFilename=PhotoStudio-Windows-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\PhotoStudio.exe
DisableProgramGroupPage=yes

[Files]
Source: "..\PhotoStudio\dist\PhotoStudio\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Photo Studio"; Filename: "{app}\PhotoStudio.exe"
Name: "{autodesktop}\Photo Studio"; Filename: "{app}\PhotoStudio.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; GroupDescription: "Дополнительно:"; Flags: unchecked

[Run]
Filename: "{app}\PhotoStudio.exe"; Description: "Открыть Photo Studio"; Flags: nowait postinstall skipifsilent
