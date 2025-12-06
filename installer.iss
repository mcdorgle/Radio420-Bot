; ============================
; Radio420-Bot Windows Installer Script
; ============================

[Setup]
AppName=Radio420 Bot
AppVersion=1.0.0
DefaultDirName={autopf}\Radio420 Bot
DefaultGroupName=Radio420 Bot
OutputDir=installer
OutputBaseFilename=Radio420-Bot-Setup
Compression=lzma
SolidCompression=yes

[Files]
Source: "dist\Radio420-Bot.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "src\config.ini"; DestDir: "{app}"; Flags: ignoreversion
Source: "src\blaze.wav"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Radio420 Bot"; Filename: "{app}\Radio420-Bot.exe"
Name: "{commondesktop}\Radio420 Bot"; Filename: "{app}\Radio420-Bot.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional options:";

[Run]
Filename: "{app}\Radio420-Bot.exe"; Description: "Launch Radio420 Bot"; Flags: nowait postinstall skipifsilent

