; Instalador do PhoneBridge (Windows) - compilado no CI com Inno Setup.
#ifndef AppVersion
  #define AppVersion "2.0.0"
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
ArchitecturesInstallMode=x64compatible
WizardStyle=modern

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "desktopicon"; Description: "Criar um atalho na área de trabalho"; Flags: unchecked
Name: "autostart"; Description: "Iniciar o PhoneBridge com o Windows (fica na bandeja)"
Name: "network"; Description: "Liberar no Firewall e definir as redes conectadas como Privadas"

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
  Flags: runhidden; StatusMsg: "Configurando o Firewall e as redes..."; Tasks: network
Filename: "{app}\PhoneBridgeServer.exe"; Description: "Abrir o PhoneBridge"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "powershell.exe"; Parameters: "-NoProfile -Command ""Get-NetFirewallRule -DisplayName 'PhoneBridge*' | Remove-NetFirewallRule"""; \
  Flags: runhidden; RunOnceId: "RemoveFirewallRules"
