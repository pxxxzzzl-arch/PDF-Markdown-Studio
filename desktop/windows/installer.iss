#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif

#ifndef SourceDir
  #define SourceDir "..\..\build\windows-app\stage\PDF Markdown Studio"
#endif

#ifndef OutputDir
  #define OutputDir "..\..\dist\windows"
#endif

#define MyAppName "PDF Markdown Studio"
#define MyAppExeName "PDF Markdown Studio.exe"
#define MyAppMutex "Local\PDFMarkdownStudio"
#define WebView2Bootstrapper "MicrosoftEdgeWebview2Setup.exe"

[Setup]
AppId={{F3E7FB87-69CF-40C7-A1E8-6F0A4ABBA694}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher=PDF Markdown Studio
AppPublisherURL=https://github.com/pxxxzzzl-arch/PDF-Markdown-Studio
AppSupportURL=https://github.com/pxxxzzzl-arch/PDF-Markdown-Studio/issues
AppUpdatesURL=https://github.com/pxxxzzzl-arch/PDF-Markdown-Studio/releases
DefaultDirName={localappdata}\Programs\PDF Markdown Studio
DefaultGroupName=PDF Markdown Studio
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
OutputDir={#OutputDir}
OutputBaseFilename=PDF-Markdown-Studio-{#MyAppVersion}-Windows-x64-Setup
SetupIconFile={#SourceDir}\PDF Markdown Studio.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/ultra64
SolidCompression=yes
LZMAUseSeparateProcess=yes
WizardStyle=modern
CloseApplications=force
RestartApplications=no
AppMutex={#MyAppMutex}
SetupLogging=yes
ChangesAssociations=no
ChangesEnvironment=no
UsePreviousAppDir=yes
UsePreviousGroup=yes

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; \
    Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\PDF Markdown Studio"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\卸载 PDF Markdown Studio"; Filename: "{uninstallexe}"
Name: "{autodesktop}\PDF Markdown Studio"; Filename: "{app}\{#MyAppExeName}"; \
    Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; \
    GroupDescription: "附加快捷方式："; Flags: unchecked

[Run]
Filename: "{app}\{#WebView2Bootstrapper}"; Parameters: "/silent /install"; \
    StatusMsg: "正在确认 Microsoft Edge WebView2 Runtime…"; \
    Flags: runhidden waituntilterminated
Filename: "{app}\{#MyAppExeName}"; Description: "启动 PDF Markdown Studio"; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Deliberately preserve %LOCALAPPDATA%\PDF Markdown Studio. It contains the
; user's PDFs, conversion results, SQLite history, caches, and diagnostics.
Type: filesandordirs; Name: "{app}"
