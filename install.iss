; Inno Setup Script for Radio420 Bot
; This script is designed for a --onefile PyInstaller build.

; --- DEFINES ---
; Use defines to easily change common values.
#define MyAppName "Radio420 Control Panel"
#define MyAppVersion "1.8"
#define MyAppPublisher "Radio420"
#define MyAppExeName "Radio420ControlPanel.exe"
#define MyOutputName "Radio420_ControlPanel_Setup"

[Setup]
; A unique ID for your application. Use Tools -> Generate GUID in Inno Setup IDE for a new one.
AppId={{F0C8E9F1-C2A3-4B56-9D01-A1B2C3D4E5F6}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
;AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputBaseFilename={#MyOutputName}
Compression=lzma
SolidCompression=yes
WizardStyle=modern
; The icon for the installer itself.
SetupIconFile=D:\Radio420-Bot\src\logo.ico
UninstallDisplayIcon={app}\{#MyAppExeName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
; Let the user choose whether to create a desktop icon.
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; This section tells the installer what to package.
; Source: "Path to your single .exe file"; DestDir: "{app}"
; Make sure the path to your .exe is correct. It should be in the 'dist' folder after PyInstaller runs.
Source: "D:\Radio420-Bot\dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
; Create Start Menu entries and the optional desktop icon.
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; Offer to run the application after the installation is complete.
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[Code]
// Helper procedure to split a string by a delimiter into a TStringList
procedure StrSplit(const S, Delimiter: string; List: TStrings);
var
  P: Integer;
  T: string;
begin
  List.Clear;
  T := S;
  P := Pos(Delimiter, T);
  while P > 0 do
  begin
    List.Add(Copy(T, 1, P - 1));
    T := Copy(T, P + Length(Delimiter), Length(T));
    P := Pos(Delimiter, T);
  end;
  if Length(T) > 0 then
    List.Add(T);
end;

function IsFFmpegInPath(): Boolean;
var
  Paths: TStringList;
  I: Integer;
  Path: String;
  FFmpegPath: String;
begin
  Result := False;
  Paths := TStringList.Create;
  try
    Path := GetEnv('PATH');
    StrSplit(Path, ';', Paths);
    
    for I := 0 to Paths.Count - 1 do
    begin
      FFmpegPath := AddBackslash(Paths[I]) + 'ffmpeg.exe';
      if FileExists(FFmpegPath) then
      begin
        Result := True;
        Exit;
      end;
    end;
  finally
    Paths.Free;
  end;
end;

function InitializeSetup(): Boolean;
var
  ResultCode: Integer;
begin
  // This function runs at the very beginning of the setup.
  Result := True; // Assume installation can proceed

  if not IsFFmpegInPath() then
  begin
    ResultCode := MsgBox('FFmpeg was not found in your system PATH.'#13#10#13#10 +
      'This is a required dependency for audio encoding and device detection.'#13#10#13#10 +
      'Please download FFmpeg and add it to your PATH.'#13#10#13#10 +
      'Do you want to open the FFmpeg download page now?',
      mbConfirmation, MB_YESNO);
    
    if ResultCode = IDYES then
    begin
      // Open the official FFmpeg download page for the user
      ShellExec('open', 'https://ffmpeg.org/download.html', '', '', SW_SHOWNORMAL, ewNoWait, ResultCode);
    end;
    // You could force the installer to abort by setting Result := False;
    // but we'll let the user continue at their own risk.
  end;
end;
