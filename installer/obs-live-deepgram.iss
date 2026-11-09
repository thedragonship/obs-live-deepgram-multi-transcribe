#define AppName "OBS Live Deepgram"
#define AppVersion GetEnv("APP_VERSION")
#if AppVersion == ""
  #define AppVersion "0.1.0"
#endif
#define AppPublisher "obs-live-deepgram-multi-transcribe"
#define AppExeName "obs-live-deepgram.exe"
#define AppSource GetEnv("APP_SOURCE")
#if AppSource == ""
  #define AppSource "..\dist\obs-live-deepgram"
#endif

[Setup]
AppId={{9C97E42B-2F30-4B42-9A64-D6E7C601D7C1}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\OBS Live Deepgram
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=..\dist-installer
OutputBaseFilename=obs-live-deepgram-setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
UninstallDisplayIcon={app}\{#AppExeName}
SetupLogging=yes

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Créer un raccourci sur le bureau"; GroupDescription: "Raccourcis :"; Flags: unchecked

[Files]
Source: "{#AppSource}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "WebView2Setup.exe"; Flags: dontcopy

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Lancer {#AppName}"; Flags: nowait postinstall skipifsilent runasoriginaluser

[Code]
const
  WebView2ClientId = '{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';

function HasWebView2At(RootKey: Integer; SubKey: String): Boolean;
var
  Version: String;
begin
  Result := RegQueryStringValue(RootKey, SubKey, 'pv', Version) and
    (Version <> '') and (Version <> '0.0.0.0');
end;

function WebView2Installed: Boolean;
var
  Key64, Key32: String;
begin
  Key64 := 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\' + WebView2ClientId;
  Key32 := 'SOFTWARE\Microsoft\EdgeUpdate\Clients\' + WebView2ClientId;
  Result := HasWebView2At(HKLM64, Key64) or HasWebView2At(HKCU64, Key64) or
    HasWebView2At(HKLM32, Key32) or HasWebView2At(HKCU32, Key32);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  Result := '';
  if not WebView2Installed then begin
    ExtractTemporaryFile('WebView2Setup.exe');
    if not Exec(ExpandConstant('{tmp}\WebView2Setup.exe'), '/silent /install', '',
      SW_HIDE, ewWaitUntilTerminated, ResultCode) then
      Result := 'Impossible de lancer l’installation du runtime Microsoft Edge WebView2.'
    else if (ResultCode <> 0) and (ResultCode <> 3010) then
      Result := 'WebView2 n’a pas pu être installé. Vérifiez la connexion Internet puis relancez l’installation.';
  end;
end;
