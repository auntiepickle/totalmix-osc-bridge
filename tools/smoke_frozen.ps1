# Smoke test for the frozen bridge (dist/tmosc-bridge/tmosc-bridge.exe).
# Starts the exe from a foreign working directory with state redirected to a
# temp dir, then checks: /api/health, example-template fallback on a fresh data
# dir, static UI served from the bundle, a config write landing in the data
# dir, and a WebSocket handshake on /ws. Exit code 0 = PASS.
param(
  [string]$Exe = "dist/tmosc-bridge/tmosc-bridge.exe",
  [int]$Port = 8098,
  [int]$TimeoutSec = 60
)
$ErrorActionPreference = "Stop"
$Exe = (Resolve-Path $Exe).Path
$data = Join-Path ([IO.Path]::GetTempPath()) ("tmosc-smoke-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $data | Out-Null

$env:TMOSC_DATA_DIR = $data
$env:WEB_PORT = "$Port"
$env:ENABLE_OSC_LISTENER = "False"
$env:ENABLE_OSC_MONITOR = "False"
$env:MQTT_BROKER = "127.0.0.1"
Remove-Item Env:OSC_IP -ErrorAction SilentlyContinue

"data-dir flag: " + (& $Exe --data-dir)
"version flag:  " + (& $Exe --version)

$log = Join-Path $data "smoke-stdout.log"
$err = Join-Path $data "smoke-stderr.log"
$p = Start-Process -FilePath $Exe -WorkingDirectory ([IO.Path]::GetTempPath()) `
       -RedirectStandardOutput $log -RedirectStandardError $err -PassThru -NoNewWindow
try {
  $deadline = (Get-Date).AddSeconds($TimeoutSec); $health = $null
  while ((Get-Date) -lt $deadline) {
    if ($p.HasExited) { throw "exe exited early with code $($p.ExitCode)" }
    try { $health = Invoke-RestMethod "http://127.0.0.1:$Port/api/health" -TimeoutSec 2; break }
    catch { Start-Sleep -Milliseconds 500 }
  }
  if ($null -eq $health) { throw "no /api/health answer within $TimeoutSec s" }
  "health: " + ($health | ConvertTo-Json -Compress)

  $s = Invoke-RestMethod "http://127.0.0.1:$Port/api/status" -TimeoutSec 5
  if ($s.mappings_source -ne "mappings.example.json") {
    throw "fresh data dir should fall back to the bundled example, got [$($s.mappings_source)]"
  }
  "templates: bundled example fallback OK"

  $idx = Invoke-WebRequest "http://127.0.0.1:$Port/static/index.html" -TimeoutSec 5 -UseBasicParsing
  if ($idx.StatusCode -ne 200 -or $idx.Content -notmatch "midi-status") { throw "static index.html not served from the bundle" }
  "static: index.html served ($($idx.Content.Length) bytes)"

  Invoke-RestMethod -Method Post "http://127.0.0.1:$Port/api/config/mappings/init-from-example" -TimeoutSec 5 | Out-Null
  if (-not (Test-Path (Join-Path $data "mappings.json"))) { throw "mappings.json was not written into TMOSC_DATA_DIR" }
  if (-not (Test-Path (Join-Path $data "bridge.log"))) { throw "bridge.log was not written into TMOSC_DATA_DIR" }
  "state: mappings.json + bridge.log in $data"

  $ws = [System.Net.WebSockets.ClientWebSocket]::new()
  $ws.ConnectAsync([Uri]"ws://127.0.0.1:$Port/ws", [Threading.CancellationTokenSource]::new(5000).Token).GetAwaiter().GetResult() | Out-Null
  if ($ws.State -ne [System.Net.WebSockets.WebSocketState]::Open) { throw "websocket not open: $($ws.State)" }
  $bytes = [Text.Encoding]::UTF8.GetBytes('{"type":"noop"}')
  $ws.SendAsync([ArraySegment[byte]]::new($bytes), [System.Net.WebSockets.WebSocketMessageType]::Text, $true,
                [Threading.CancellationToken]::None).GetAwaiter().GetResult() | Out-Null
  $ws.CloseAsync([System.Net.WebSockets.WebSocketCloseStatus]::NormalClosure, "bye",
                 [Threading.CancellationTokenSource]::new(5000).Token).GetAwaiter().GetResult() | Out-Null
  "websocket: handshake + send + close OK"
  "SMOKE PASS"
}
finally {
  if (-not $p.HasExited) { Stop-Process -Id $p.Id -Force }
  Start-Sleep -Milliseconds 300
  "--- exe stdout (tail) ---"; Get-Content $log -Tail 12 -ErrorAction SilentlyContinue
  "--- exe stderr (tail) ---"; Get-Content $err -Tail 12 -ErrorAction SilentlyContinue
  Remove-Item $data -Recurse -Force -ErrorAction SilentlyContinue
}
