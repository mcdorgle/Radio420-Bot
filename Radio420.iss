; Radio420 Bot Inno Setup Script
; See https://jrsoftware.org/ishelp/ for documentation

[Setup]
; --- Basic Application Info ---
AppName=Radio420 Bot
AppVersion=1.8
AppPublisher=mcdorgle
AppPublisherURL=https://github.com/mcdorgle/Radio420-Bot
AppSupportURL=https://github.com/mcdorgle/Radio420-Bot/issues
AppWebSite=https://github.com/mcdorgle/Radio420-Bot

; --- Installation Directories ---
DefaultDirName={autopf}\Radio420 Bot
DefaultGroupName=Radio420 Bot
DisableDirPage=no

; --- Output Installer File ---
OutputBaseFilename=Radio420-Bot-v1.8-setup
OutputDir=.\installers
Compression=lzma
SolidCompression=yes

; --- Installer Appearance ---
WizardStyle=modern
SetupIconFile=src\logo.ico
UninstallDisplayIcon={app}\Radio420.exe

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; This is the main executable built by PyInstaller.
; It must exist in the 'dist' folder before you compile this script.
Source: "dist\Radio420.exe"; DestDir: "{app}"; Flags: ignoreversion

; Include the README file to be shown after installation.
Source: "README.md"; DestDir: "{app}"; Flags: isreadme

[Icons]
Name: "{group}\Radio420 Bot"; Filename: "{app}\Radio420.exe"
Name: "{group}\{cm:UninstallProgram,Radio420 Bot}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Radio420 Bot"; Filename: "{app}\Radio420.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Radio420.exe"; Description: "{cm:LaunchProgram,Radio420 Bot}"; Flags: nowait postinstall skipifsilent

[Code]
// Show a message box reminding the user about the FFmpeg prerequisite.
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    MsgBox('Remember: For Shoutcast/Icecast encoding to work, you must have FFmpeg installed and added to your system''s PATH environment variable.', mbInformation, MB_OK);
  end;
end;