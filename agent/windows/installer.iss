; TotalMix OSC Agent — Windows installer with a configuration wizard.
; Compile:  iscc /DAppVersion=0.3.0 installer.iss
; The two signed exes (tmosc-agent-tray.exe, tmosc-agent.exe) and tray.ico must
; sit next to this .iss at compile time (CI stages them there). Per-user install
; (no admin); writes %APPDATA%\tmosc-agent\config.txt from the wizard answers.

#ifndef AppVersion
  #define AppVersion "0.0.0-dev"
#endif
#define AppName "TotalMix OSC Agent"
#define Publisher "Chris Gross"
#define ExeTray "tmosc-agent-tray.exe"
#define ExeConsole "tmosc-agent.exe"

[Setup]
AppId={{9F5B2E7A-3C41-4E9D-9B77-7A1C0B0F5D21}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#Publisher}
DefaultDirName={autopf}\{#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=dist
OutputBaseFilename=tmosc-agent-setup
SetupIconFile=tray.ico
UninstallDisplayIcon={app}\{#ExeTray}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

[Files]
Source: "{#ExeTray}";    DestDir: "{app}"; Flags: ignoreversion
Source: "{#ExeConsole}"; DestDir: "{app}"; Flags: ignoreversion
; a throwaway copy so the wizard can run `--list` before install
Source: "{#ExeConsole}"; Flags: dontcopy

[Icons]
Name: "{autoprograms}\{#AppName}";           Filename: "{app}\{#ExeTray}"
Name: "{autostartup}\{#AppName}";            Filename: "{app}\{#ExeTray}"; Tasks: startup

[Tasks]
Name: startup; Description: "Start {#AppName} automatically when I sign in"

[Run]
Filename: "{app}\{#ExeTray}"; Description: "Launch {#AppName} now"; Flags: nowait postinstall skipifsilent

[Code]
var
  Page: TWizardPage;
  EditHost, EditPort, EditHttps: TNewEdit;
  MidiCombo: TNewComboBox;

procedure PopulateMidi;
var
  ExeTmp, OutFile, Content: String;
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
    begin
      if LoadStringsFromFile(OutFile, Lines) then
      begin
        for i := 0 to GetArrayLength(Lines) - 1 do
        begin
          s := Trim(Lines[i]);
          // rows look like: "0     UFX II Midi Port 1" — starts with a digit
          if (s <> '') and (s[1] >= '0') and (s[1] <= '9') then
          begin
            // drop the leading index + following whitespace, keep the name
            while (Length(s) > 0) and (s[1] >= '0') and (s[1] <= '9') do Delete(s, 1, 1);
            name := Trim(s);
            if name <> '' then MidiCombo.Items.Add(name);
          end;
        end;
      end;
    end;
  except
  end;
  MidiCombo.ItemIndex := 0;
end;

procedure AddLabel(ACaption: String; ATop: Integer);
var
  L: TNewStaticText;
begin
  L := TNewStaticText.Create(Page);
  L.Parent := Page.Surface;
  L.Left := 0;
  L.Top := ATop;
  L.Caption := ACaption;
end;

procedure InitializeWizard;
begin
  Page := CreateCustomPage(wpSelectDir, 'Bridge connection',
    'Point the agent at your TotalMix OSC bridge and pick the MIDI controller on this PC.');

  AddLabel('Bridge host or IP:', 0);
  EditHost := TNewEdit.Create(Page);
  EditHost.Parent := Page.Surface;
  EditHost.Top := 16; EditHost.Width := Page.SurfaceWidth;
  EditHost.Text := '192.168.1.41';

  AddLabel('Bridge port:', 48);
  EditPort := TNewEdit.Create(Page);
  EditPort.Parent := Page.Surface;
  EditPort.Top := 64; EditPort.Width := ScaleX(120);
  EditPort.Text := '8088';

  AddLabel('MIDI controller (leave as first input if unsure):', 96);
  MidiCombo := TNewComboBox.Create(Page);
  MidiCombo.Parent := Page.Surface;
  MidiCombo.Top := 112; MidiCombo.Width := Page.SurfaceWidth;
  MidiCombo.Style := csDropDown;   // editable + list

  AddLabel('Secure (HTTPS) web UI URL — optional:', 144);
  EditHttps := TNewEdit.Create(Page);
  EditHttps.Parent := Page.Surface;
  EditHttps.Top := 160; EditHttps.Width := Page.SurfaceWidth;
  EditHttps.Text := '';

  PopulateMidi;
end;

function MidiValue: String;
begin
  // index 0 is the "(first available input)" sentinel -> empty (= first input)
  if (MidiCombo.ItemIndex = 0) and (MidiCombo.Text = MidiCombo.Items[0]) then
    Result := ''
  else
    Result := Trim(MidiCombo.Text);
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Dir, Cfg: String;
begin
  if CurStep = ssPostInstall then
  begin
    Dir := ExpandConstant('{userappdata}\tmosc-agent');
    ForceDirectories(Dir);
    Cfg := 'host=' + Trim(EditHost.Text) + #13#10 +
           'port=' + Trim(EditPort.Text) + #13#10 +
           'midi=' + MidiValue + #13#10;
    if Trim(EditHttps.Text) <> '' then
      Cfg := Cfg + 'https_url=' + Trim(EditHttps.Text) + #13#10;
    SaveStringToFile(Dir + '\config.txt', Cfg, False);
  end;
end;
