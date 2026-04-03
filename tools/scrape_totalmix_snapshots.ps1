# ========================================================
# CONFIGURATION - EDIT THESE TWO LINES FOR YOUR SETUP
# ========================================================
if ($env:TOTALMIX_FOLDER) {
    $TOTALMIX_FOLDER = $env:TOTALMIX_FOLDER
} else {
    $TOTALMIX_FOLDER = Join-Path $env:LOCALAPPDATA "TotalMixFX"
}

if ($env:SERVER_SHARE) {
    $SERVER_SHARE = $env:SERVER_SHARE
} else {
    $SERVER_SHARE = "\\PRECIOUS\public\Scripts\totalmix\ufx2_snapshot_map.json"
}

# ========================================================
# END OF CONFIGURATION
# ========================================================

Write-Host "TotalMix Snapshot Scraper (Grok Home Studio Project - public edition) - WITH SLOT FIX" -ForegroundColor Cyan

# 1. Workspace names — collected in PresetName order (1,2,3...) to match TotalMix UI
$prefFile = Join-Path $TOTALMIX_FOLDER "rme.totalmix.preferences.xml"
$workspaceList = @()   # ordered list
$workspaceMap = @{}
if (Test-Path $prefFile) {
    $text = Get-Content $prefFile -Raw
    $matches = [regex]::Matches($text, '<val e="PresetName(\d+)" v="([^"]*)"/>')
    foreach ($m in $matches) {
        $num = [int]$m.Groups[1].Value
        $name = $m.Groups[2].Value.Trim()
        if ($name -and $name -ne "<Empty>" -and $name -ne "") {
            $workspaceList += $name
            $workspaceMap[$name] = $num
        }
    }
    Write-Host "Found $($workspaceList.Count) workspaces in UI order: $($workspaceList -join ', ')" -ForegroundColor Green
}

# 2. Snapshot names per workspace + real physical slot number (the fix)
$snapshotMap = @{}
foreach ($wsName in $workspaceList) {
    $wsNum = $workspaceMap[$wsName]
    $presetFile = Join-Path $TOTALMIX_FOLDER "preset$wsNum.tmws"
    if (-not (Test-Path $presetFile)) { continue }

    $text = Get-Content $presetFile -Raw
    $snaps = @{}
    $matches = [regex]::Matches($text, '<val e="SnapshotName (\d+)" v="([^"]*)"/>')
    foreach ($m in $matches) {
        $idx = [int]$m.Groups[1].Value + 1
        $name = $m.Groups[2].Value.Trim()
        $snaps["$idx"] = if ($name) { $name } else { "Empty $idx" }
    }
    1..8 | ForEach-Object {
        if (-not $snaps.ContainsKey("$_")) { $snaps["$_"] = "Empty" }
    }

    # NEW STRUCTURE: real TotalMix slot + nested snapshots
    $snapshotMap[$wsName] = @{
        slot      = $wsNum
        snapshots = $snaps
    }

    Write-Host "OK $wsName (slot $wsNum) -> $($snaps.Values -join ', ')" -ForegroundColor Green
}

# 3. Output JSON + backup + copy to server
if ($snapshotMap.Count -gt 0) {
    $jsonOutput = $snapshotMap | ConvertTo-Json -Depth 10
    Write-Output $jsonOutput

    $backupFile = Join-Path $TOTALMIX_FOLDER "ufx2_snapshot_map.json"
    $jsonOutput | Out-File -FilePath $backupFile -Encoding utf8
    Write-Host "Local backup saved to $backupFile" -ForegroundColor Green

    try {
        $jsonOutput | Out-File -FilePath $SERVER_SHARE -Encoding utf8 -Force
        Write-Host "COPIED map to server: $SERVER_SHARE (now with real slot numbers!)" -ForegroundColor Green
    } catch {
        Write-Host "COPY FAILED: $($_.Exception.Message)" -ForegroundColor Red
    }
} else {
    Write-Output "{}"
    Write-Host "No snapshot data found" -ForegroundColor Red
}

Write-Host "Scraper finished." -ForegroundColor Cyan