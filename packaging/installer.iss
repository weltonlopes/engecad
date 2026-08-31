; Build: iscc packaging\installer.iss /DMyAppVersion=0.2.0
; (o build.ps1 já passa a versão automaticamente, lida de engecad/__init__.py)
;
; AppId é fixo para sempre - é o que o Windows usa para saber que uma nova
; versão é uma ATUALIZAÇÃO da mesma instalação, e não um programa novo.
; Nunca gere um novo GUID depois que a primeira versão for distribuída.
#define MyAppName "EngeCAD"
#define MyAppPublisher "EngeCAD"
#define MyAppURL "https://github.com/"
#define MyAppExeName "EngeCAD.exe"
#ifndef MyAppVersion
  #define MyAppVersion "0.0.0-dev"
#endif

[Setup]
AppId={{C40A5689-D966-4B30-85FD-5EF6902ECE6F}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; Um instalador só, sem exigir admin (instala em Program Files do usuário
; se não tiver privilégio) - evita prompt de UAC pra quem não tem admin.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=commandline dialog
OutputDir=..\dist\installer
OutputBaseFilename=EngeCAD-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#MyAppExeName}
#if FileExists("icon.ico")
SetupIconFile=icon.ico
#endif
; Instalador único assinado seria ideal aqui (SignTool) para evitar o aviso
; do SmartScreen; deixado de fora até haver certificado de assinatura.

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "desktopicon"; Description: "Criar ícone na área de trabalho"; GroupDescription: "Ícones adicionais:"
Name: "dxfassoc"; Description: "Abrir arquivos .dxf com o EngeCAD"; GroupDescription: "Associação de arquivos:"; Flags: unchecked

[Files]
Source: "..\dist\EngeCAD\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Desinstalar {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
Root: HKA; Subkey: "Software\Classes\.dxf\OpenWithProgids"; ValueType: string; ValueName: "EngeCAD.dxf"; ValueData: ""; Tasks: dxfassoc; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\EngeCAD.dxf"; ValueType: string; ValueName: ""; ValueData: "Desenho DXF"; Tasks: dxfassoc; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\EngeCAD.dxf\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#MyAppExeName},0"; Tasks: dxfassoc
Root: HKA; Subkey: "Software\Classes\EngeCAD.dxf\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%1"""; Tasks: dxfassoc

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Abrir {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"
