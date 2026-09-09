; TotalMix OSC - unified Windows installer: Client (tray MIDI agent), Server
; (the frozen bridge), or Both. Per-user by default (no admin needed); an
; elevated install additionally opens the Windows Firewall for the bridge.
;
; Compile (CI does this):  iscc /DAppVersion=1.2.3 tmosc-setup.iss
; Inputs, staged by CI relative to this file:
;   ..\agent\windows\tmosc-agent-tray.exe, ..\agent\windows\tmosc-agent.exe   (signed)
;   ..\dist\tmosc-bridge\**   (signed PyInstaller onedir build of the bridge)
; Writes on install:
;   %APPDATA%\tmosc-agent\config.txt   (client)  - host/port/midi/https_url
;   %APPDATA%\tmosc-bridge\config.env  (server)  - OSC_IP, ports, transport, MQTT
; Existing values are read back into the wizard so an upgrade keeps your setup.

#ifndef AppVersion
  #define AppVersion "0.0.0-dev"
#endif
#define AppName "TotalMix OSC"
#define Publisher "Chris Gross"
#define AgentDir "..\agent\windows"
#define BridgeDir "..\dist\tmosc-bridge"
#define ExeTray "tmosc-agent-tray.exe"
#define ExeConsole "tmosc-agent.exe"
#define ExeBridge "tmosc-bridge.exe"

[Setup]
AppId={{6D2F1C3B-8A47-4B1E-9C55-2E7D0A9F4B31}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#Publisher}
DefaultDirName={autopf}\{#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=dist
OutputBaseFilename=tmosc-setup
SetupIconFile={#AgentDir}\tray.ico
UninstallDisplayIcon={app}\agent\{#ExeTray}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

[Types]
Name: "full";   Description: "Client + Server  -  this PC runs TotalMix FX and hosts the bridge"
Name: "client"; Description: "Client only  -  tray MIDI agent; the bridge runs on another machine"
Name: "server"; Description: "Server only  -  the bridge; controllers connect from elsewhere"
Name: "custom"; Description: "Custom"; Flags: iscustom

[Components]
Name: "client"; Description: "Tray MIDI agent (drives your MIDI mapping with no browser open)"; Types: full client
Name: "server"; Description: "Bridge server (OSC to TotalMix, web UI, MQTT)";                   Types: full server

[Files]
Source: "{#AgentDir}\{#ExeTray}";    DestDir: "{app}\agent";  Flags: ignoreversion; Components: client
Source: "{#AgentDir}\{#ExeConsole}"; DestDir: "{app}\agent";  Flags: ignoreversion; Components: client
Source: "{#BridgeDir}\*";            DestDir: "{app}\bridge"; Flags: ignoreversion recursesubdirs createallsubdirs; Components: server
; throwaway copy so the client wizard page can run --list / --discover before install
Source: "{#AgentDir}\{#ExeConsole}"; Flags: dontcopy

[Icons]
Name: "{autoprograms}\{#AppName} Agent";  Filename: "{app}\agent\{#ExeTray}"; Components: client
Name: "{autoprograms}\{#AppName} Bridge"; Filename: "{app}\bridge\{#ExeBridge}"; WorkingDir: "{app}\bridge"; Flags: runminimized; Components: server
Name: "{autoprograms}\{#AppName} Web UI"; Filename: "{code:WebUrl}"; Components: server
Name: "{autostartup}\{#AppName} Agent";   Filename: "{app}\agent\{#ExeTray}"; Tasks: startup_client
Name: "{autostartup}\{#AppName} Bridge";  Filename: "{app}\bridge\{#ExeBridge}"; WorkingDir: "{app}\bridge"; Flags: runminimized; Tasks: startup_server

[Tasks]
Name: startup_client; Description: "Start the tray agent when I sign in"; Components: client
Name: startup_server; Description: "Start the bridge when I sign in";     Components: server

[Run]
; elevated install only: open the firewall so the web UI and TotalMix's OSC feedback reach the bridge over the LAN
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""TotalMix OSC Bridge"" dir=in action=allow program=""{app}\bridge\{#ExeBridge}"" enable=yes"; Flags: runhidden; Components: server; Check: IsAdminInstallMode
Filename: "{app}\bridge\{#ExeBridge}"; WorkingDir: "{app}\bridge"; Description: "Start the bridge now"; Flags: nowait postinstall skipifsilent runminimized; Components: server
Filename: "{app}\agent\{#ExeTray}"; Description: "Start the tray agent now"; Flags: nowait postinstall skipifsilent; Components: client

[UninstallRun]
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""TotalMix OSC Bridge"""; Flags: runhidden; Components: server; Check: IsAdminInstallMode; RunOnceId: "tmoscfw"

[Code]
var
  SrvPage, MqttPage, CliPage: TWizardPage;
  EOscIp, EOscPort, EListenPort, EWebPort: TNewEdit;
  CTransport: TNewComboBox;
  EMqttHost, EMqttPort, EMqttUser, EMqttPass: TNewEdit;
  EHost, EPort, EHttps: TNewEdit;
  MidiCombo: TNewComboBox;
  ClientPrefilled: Boolean;

// ---- helpers ---------------------------------------------------------------

function ReadKey(const FileName, Key, Default: String): String;
var
  Lines: TArrayOfString;
  i, p: Integer;
  s, k: String;
begin
  Result := Default;
  if not LoadStringsFromFile(FileName, Lines) then exit;
  for i := 0 to GetArrayLength(Lines) - 1 do
  begin
    s := Trim(Lines[i]);
    if (s = '') or (s[1] = '#') then continue;
    p := Pos('=', s);
    if p = 0 then continue;
    k := Trim(Copy(s, 1, p - 1));
    if k = Key then
    begin
      Result := Trim(Copy(s, p + 1, Length(s)));
      exit;
    end;
  end;
end;

function IsPort(const S: String): Boolean;
var
  n: Integer;
begin
  n := StrToIntDef(Trim(S), -1);
  Result := (n > 0) and (n < 65536);
end;

function WebUrl(Param: String): String;
begin
  Result := 'http://localhost:' + Trim(EWebPort.Text) + '/';
end;

function AddLabel(Page: TWizardPage; const ACaption: String; ATop: Integer): TNewStaticText;
begin
  Result := TNewStaticText.Create(Page);
  Result.Parent := Page.Surface;
  Result.Left := 0;
  Result.Top := ScaleY(ATop);
  Result.Caption := ACaption;
end;

function AddEdit(Page: TWizardPage; ATop, ALeft, AWidth: Integer; const AText: String): TNewEdit;
begin
  Result := TNewEdit.Create(Page);
  Result.Parent := Page.Surface;
  Result.Left := ScaleX(ALeft);
  Result.Top := ScaleY(ATop);
  if AWidth > 0 then Result.Width := ScaleX(AWidth) else Result.Width := Page.SurfaceWidth;
  Result.Text := AText;
end;

// ---- client page helpers (same as the client-only installer) ---------------

procedure PopulateMidi;
var
  ExeTmp, OutFile: String;
  Lines: TArrayOfString;
  i, code: Integer;
  s, name: String;
begin
  MidiCombo.Items.Clear;
  MidiCombo.Items.Add('(first available input)');
  try
    ExtractTemporaryFile('{#ExeConsole}');
    ExeTmp := ExpandConstant('{tmp}\{#ExeConsole}');
    OutFile := ExpandConstant('{tmp}\midilist.txt');
    if Exec(ExpandConstant('{cmd}'), '/C ""' + ExeTmp + '" --list > "' + OutFile + '" 2>&1"',
            '', SW_HIDE, ewWaitUntilTerminated, code) then
      if LoadStringsFromFile(OutFile, Lines) then
        for i := 0 to GetArrayLength(Lines) - 1 do
        begin
          s := Trim(Lines[i]);
          // rows look like: "0     UFX II Midi Port 1" - starts with a digit
          if (s <> '') and (s[1] >= '0') and (s[1] <= '9') then
          begin
            while (Length(s) > 0) and (s[1] >= '0') and (s[1] <= '9') do Delete(s, 1, 1);
            name := Trim(s);
            if name <> '' then MidiCombo.Items.Add(name);
          end;
        end;
  except
  end;
  MidiCombo.ItemIndex := 0;
end;

function DiscoverHost: String;
var
  ExeTmp, OutFile: String;
  Lines: TArrayOfString;
  code: Integer;
begin
  Result := '';
  try
    ExtractTemporaryFile('{#ExeConsole}');
    ExeTmp := ExpandConstant('{tmp}\{#ExeConsole}');
    OutFile := ExpandConstant('{tmp}\disc.txt');
    if Exec(ExpandConstant('{cmd}'), '/C ""' + ExeTmp + '" --discover > "' + OutFile + '" 2>nul"',
            '', SW_HIDE, ewWaitUntilTerminated, code) then
      if (code = 0) and LoadStringsFromFile(OutFile, Lines) and (GetArrayLength(Lines) > 0) then
        Result := Trim(Lines[0]);
  except
  end;
end;

function MidiValue: String;
begin
  // index 0 is the "(first available input)" sentinel -> empty (= first input)
  if (MidiCombo.ItemIndex = 0) and (MidiCombo.Text = MidiCombo.Items[0]) then
    Result := ''
  else
    Result := Trim(MidiCombo.Text);
end;

// ---- wizard ----------------------------------------------------------------

procedure InitializeWizard;
var
  SrvCfg, CliCfg, Transport: String;
begin
  SrvCfg := ExpandConstant('{userappdata}\tmosc-bridge\config.env');
  CliCfg := ExpandConstant('{userappdata}\tmosc-agent\config.txt');

  // -- Server page 1: TotalMix + web --
  SrvPage := CreateCustomPage(wpSelectComponents, 'Bridge server',
    'Where is TotalMix FX, and how should the bridge talk to it?');
  AddLabel(SrvPage, 'IP of the PC running TotalMix FX (127.0.0.1 if it is this PC):', 0);
  EOscIp := AddEdit(SrvPage, 16, 0, 0, ReadKey(SrvCfg, 'OSC_IP', '127.0.0.1'));
  AddLabel(SrvPage, 'TotalMix OSC ports:   "Port incoming" (bridge sends)      "Port outgoing" (bridge listens)', 48);
  EOscPort    := AddEdit(SrvPage, 64, 0, 110, ReadKey(SrvCfg, 'OSC_PORT', '7001'));
  EListenPort := AddEdit(SrvPage, 64, 200, 110, ReadKey(SrvCfg, 'OSC_LISTEN_PORT', '9001'));
  AddLabel(SrvPage, 'Web UI port (http://localhost:<port>):', 96);
  EWebPort := AddEdit(SrvPage, 112, 0, 110, ReadKey(SrvCfg, 'WEB_PORT', '8088'));
  AddLabel(SrvPage, 'OSC transport:', 144);
  CTransport := TNewComboBox.Create(SrvPage);
  CTransport.Parent := SrvPage.Surface;
  CTransport.Top := ScaleY(160); CTransport.Width := SrvPage.SurfaceWidth;
  CTransport.Style := csDropDownList;
  CTransport.Items.Add('classic  -  TotalMix OSC remote 1 (default, all TotalMix versions)');
  CTransport.Items.Add('global   -  Global OSC remote 2 (TotalMix FX 2.1+, absolute addressing)');
  Transport := ReadKey(SrvCfg, 'OSC_TRANSPORT', 'classic');
  if Transport = 'global' then CTransport.ItemIndex := 1 else CTransport.ItemIndex := 0;

  // -- Server page 2: MQTT (optional) --
  MqttPage := CreateCustomPage(SrvPage.ID, 'MQTT (optional)',
    'Home Assistant / MQTT integration. Leave the broker blank to run without MQTT.');
  AddLabel(MqttPage, 'MQTT broker host or IP (blank = MQTT off):', 0);
  EMqttHost := AddEdit(MqttPage, 16, 0, 0, ReadKey(SrvCfg, 'MQTT_BROKER', ''));
  AddLabel(MqttPage, 'Broker port:', 48);
  EMqttPort := AddEdit(MqttPage, 64, 0, 110, ReadKey(SrvCfg, 'MQTT_PORT', '1883'));
  AddLabel(MqttPage, 'Username (optional):', 96);
  EMqttUser := AddEdit(MqttPage, 112, 0, 0, ReadKey(SrvCfg, 'MQTT_USER', ''));
  AddLabel(MqttPage, 'Password (optional):', 144);
  EMqttPass := AddEdit(MqttPage, 160, 0, 0, ReadKey(SrvCfg, 'MQTT_PASS', ''));
  EMqttPass.PasswordChar := '*';

  // -- Client page --
  CliPage := CreateCustomPage(MqttPage.ID, 'Tray agent',
    'Point the agent at your bridge and pick the MIDI controller on this PC.');
  AddLabel(CliPage, 'Bridge host or IP (auto-detected if the bridge is running):', 0);
  EHost := AddEdit(CliPage, 16, 0, 0, ReadKey(CliCfg, 'host', ''));
  AddLabel(CliPage, 'Bridge port:', 48);
  EPort := AddEdit(CliPage, 64, 0, 110, ReadKey(CliCfg, 'port', '8088'));
  AddLabel(CliPage, 'MIDI controller (leave as first input if unsure):', 96);
  MidiCombo := TNewComboBox.Create(CliPage);
  MidiCombo.Parent := CliPage.Surface;
  MidiCombo.Top := ScaleY(112); MidiCombo.Width := CliPage.SurfaceWidth;
  MidiCombo.Style := csDropDown;   // editable + list
  AddLabel(CliPage, 'Secure (HTTPS) web UI URL - optional:', 144);
  EHttps := AddEdit(CliPage, 160, 0, 0, ReadKey(CliCfg, 'https_url', ''));
  PopulateMidi;
  if ReadKey(CliCfg, 'midi', '') <> '' then MidiCombo.Text := ReadKey(CliCfg, 'midi', '');
  ClientPrefilled := False;
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := False;
  if (PageID = SrvPage.ID) or (PageID = MqttPage.ID) then
    Result := not WizardIsComponentSelected('server')
  else if PageID = CliPage.ID then
    Result := not WizardIsComponentSelected('client');
end;

procedure CurPageChanged(CurPageID: Integer);
var
  Found: String;
begin
  if (CurPageID = CliPage.ID) and not ClientPrefilled then
  begin
    ClientPrefilled := True;
    if WizardIsComponentSelected('server') then
    begin
      // both on this PC: the agent talks to the local bridge
      EHost.Text := '127.0.0.1';
      EPort.Text := Trim(EWebPort.Text);
    end
    else if Trim(EHost.Text) = '' then
    begin
      Found := DiscoverHost;               // auto-find a bridge on the LAN
      if Found <> '' then EHost.Text := Found;
    end;
  end;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if CurPageID = SrvPage.ID then
  begin
    if Trim(EOscIp.Text) = '' then begin MsgBox('Enter the IP of the PC running TotalMix FX.', mbError, MB_OK); Result := False; exit; end;
    if not (IsPort(EOscPort.Text) and IsPort(EListenPort.Text) and IsPort(EWebPort.Text)) then
      begin MsgBox('Ports must be numbers between 1 and 65535.', mbError, MB_OK); Result := False; exit; end;
  end
  else if CurPageID = MqttPage.ID then
  begin
    if (Trim(EMqttHost.Text) <> '') and not IsPort(EMqttPort.Text) then
      begin MsgBox('The MQTT port must be a number between 1 and 65535.', mbError, MB_OK); Result := False; exit; end;
  end
  else if CurPageID = CliPage.ID then
  begin
    if Trim(EHost.Text) = '' then begin MsgBox('Enter the bridge host or IP.', mbError, MB_OK); Result := False; exit; end;
    if not IsPort(EPort.Text) then begin MsgBox('The bridge port must be a number between 1 and 65535.', mbError, MB_OK); Result := False; exit; end;
  end;
end;

procedure WriteServerConfig;
var
  Dir, Cfg, Ip: String;
begin
  Dir := ExpandConstant('{userappdata}\tmosc-bridge');
  ForceDirectories(Dir);
  Ip := Trim(EOscIp.Text);
  Cfg := '# TotalMix OSC bridge - written by the installer (' + '{#AppVersion}' + '). Edit freely; restart the bridge to apply.' + #13#10 +
         'OSC_IP=' + Ip + #13#10 +
         'OSC_PORT=' + Trim(EOscPort.Text) + #13#10 +
         'OSC_LISTEN_PORT=' + Trim(EListenPort.Text) + #13#10 +
         'WEB_PORT=' + Trim(EWebPort.Text) + #13#10;
  if CTransport.ItemIndex = 1 then
    Cfg := Cfg + 'OSC_TRANSPORT=global' + #13#10 +
                 'GLOBAL_OSC_IP=' + Ip + #13#10 +
                 'GLOBAL_OSC_PORT=' + ReadKey(Dir + '\config.env', 'GLOBAL_OSC_PORT', '7002') + #13#10 +
                 'GLOBAL_OSC_LISTEN_PORT=' + ReadKey(Dir + '\config.env', 'GLOBAL_OSC_LISTEN_PORT', '9002') + #13#10
  else
    Cfg := Cfg + 'OSC_TRANSPORT=classic' + #13#10;
  if Trim(EMqttHost.Text) <> '' then
  begin
    Cfg := Cfg + 'ENABLE_MQTT=True' + #13#10 +
                 'MQTT_BROKER=' + Trim(EMqttHost.Text) + #13#10 +
                 'MQTT_PORT=' + Trim(EMqttPort.Text) + #13#10;
    if Trim(EMqttUser.Text) <> '' then Cfg := Cfg + 'MQTT_USER=' + Trim(EMqttUser.Text) + #13#10;
    if Trim(EMqttPass.Text) <> '' then Cfg := Cfg + 'MQTT_PASS=' + Trim(EMqttPass.Text) + #13#10;
  end
  else
    Cfg := Cfg + 'ENABLE_MQTT=False' + #13#10;
  SaveStringToFile(Dir + '\config.env', Cfg, False);
end;

procedure WriteClientConfig;
var
  Dir, Cfg: String;
begin
  Dir := ExpandConstant('{userappdata}\tmosc-agent');
  ForceDirectories(Dir);
  Cfg := 'host=' + Trim(EHost.Text) + #13#10 +
         'port=' + Trim(EPort.Text) + #13#10 +
         'midi=' + MidiValue + #13#10;
  if Trim(EHttps.Text) <> '' then
    Cfg := Cfg + 'https_url=' + Trim(EHttps.Text) + #13#10;
  SaveStringToFile(Dir + '\config.txt', Cfg, False);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    if WizardIsComponentSelected('server') then WriteServerConfig;
    if WizardIsComponentSelected('client') then WriteClientConfig;
  end;
end;
