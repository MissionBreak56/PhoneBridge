; Instalador do PhoneBridge (Windows) - compilado no CI com Inno Setup.
#ifndef AppVersion
  #define AppVersion "3.1.0"
#endif

[Setup]
AppId={{6B0B8D52-2D1F-4B7A-9E0C-5B8F3C7E1A44}
AppName=PhoneBridge
AppVersion={#AppVersion}
AppPublisher=MissionBreak56
DefaultDirName={autopf}\PhoneBridge
DefaultGroupName=PhoneBridge
UninstallDisplayIcon={app}\PhoneBridgeServer.exe
OutputDir=..\dist-installer
OutputBaseFilename=PhoneBridgeSetup
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64
WizardStyle=modern
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "desktopicon"; Description: "Criar um atalho na área de trabalho"; Flags: unchecked
Name: "autostart"; Description: "Iniciar o PhoneBridge com o Windows (fica na bandeja)"; Flags: unchecked
Name: "firewall"; Description: "Criar as regras do Firewall do Windows (somente rede local)"
Name: "privatenet"; Description: "Mudar as redes conectadas de Pública para Privada (só em redes de confiança)"; Flags: unchecked

[Files]
Source: "..\pc\dist\PhoneBridgeServer.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "setup-network.ps1"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\PhoneBridge"; Filename: "{app}\PhoneBridgeServer.exe"
Name: "{autodesktop}\PhoneBridge"; Filename: "{app}\PhoneBridgeServer.exe"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "PhoneBridge"; \
  ValueData: """{app}\PhoneBridgeServer.exe"" --tray"; Flags: uninsdeletevalue; Tasks: autostart

[Run]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""{app}\setup-network.ps1"""; \
  Flags: runhidden; StatusMsg: "Criando as regras do Firewall..."; Tasks: firewall
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""{app}\setup-network.ps1"" -Private"; \
  Flags: runhidden; StatusMsg: "Ajustando as redes..."; Tasks: privatenet
Filename: "{app}\PhoneBridgeServer.exe"; Description: "Abrir o PhoneBridge"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; remove somente as regras com os nomes do PhoneBridge; as configurações e os celulares pareados (AppData) ficam
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""{app}\setup-network.ps1"" -RemoveOnly"; \
  Flags: runhidden; RunOnceId: "RemoveFirewallRules"
