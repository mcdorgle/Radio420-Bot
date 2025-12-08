; Inno Setup Script for Radio420 Bot
; Save this file as installer_script.iss

; --- DEFINES ---
; Use defines to easily change common values.
#define MyAppName "Radio420 Bot"
#define MyAppVersion "1.7"
#define MyAppPublisher "Your Name Here"
#define MyAppExeName "Radio420Bot.exe"
#define MyOutputName "Radio420Bot_Setup"

[Setup]
; A unique ID for your application. Use Tools -> Generate GUID in Inno Setup IDE for a new one.
AppId={{F0C8E9F1-C2A3-4B56-9D01-A1B2C3D4E5F6}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; The folder created by PyInstaller in --onedir mode.
; IMPORTANT: Make sure this path is correct!
SourceDir=D:\Radio420-Bot\dist\Radio420Bot
OutputBaseFilename={#MyOutputName}
Compression=lzma
SolidCompression=yes
WizardStyle=modern
; The icon for the installer itself.
SetupIconFile=D:\Radio420-Bot\src\logo.ico

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
; Let the user choose whether to create a desktop icon.
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; This is the most important section. It tells the installer what to package.
; Source: "The folder from PyInstaller\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs
; This one line copies EVERYTHING from your PyInstaller dist folder into the user's installation directory.
Source: "*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs

[Icons]
; Create Start Menu entries and the optional desktop icon.
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; Offer to run the application after the installation is complete.
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
