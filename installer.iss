; Inno Setup — instalador do Vox para Windows
; Gere com: ISCC.exe installer.iss
[Setup]
AppName=Vox
AppVersion=1.0.0
AppPublisher=Vox
DefaultDirName={autopf}\Vox
DefaultGroupName=Vox
OutputBaseFilename=Vox_Setup
OutputDir=dist
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\Vox.exe
ChangesEnvironment=no
PrivilegesRequiredOverridesAllowed=dialog

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Criar atalho na área de trabalho"; GroupDescription: "Atalhos:"; Flags: checkedonce

[Files]
Source: "dist\Vox\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "dist\WebView2Bootstrapper.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall; Check: not IsWebView2Installed

[Icons]
Name: "{group}\Vox"; Filename: "{app}\Vox.exe"
Name: "{autodesktop}\Vox"; Filename: "{app}\Vox.exe"; Tasks: desktopicon

[Code]
function IsWebView2Installed(): Boolean;
var
  Version: String;
begin
  Result := RegQueryStringValue(HKLM,
    'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}',
    'pv', Version);
end;

[Run]
Filename: "{tmp}\WebView2Bootstrapper.exe"; Parameters: "/silent /install"; StatusMsg: "Instalando o WebView2 Runtime..."; Flags: waituntilterminated; Check: not IsWebView2Installed
Filename: "{app}\Vox.exe"; Description: "Abrir o Vox"; Flags: nowait postinstall skipifsilent
