# Keeps the Home launcher on The Frame: opens it when the TV turns on, and again when you leave an app.
# Runs in the background from a scheduled task. Only works while this laptop is on and on the home Wi-Fi.

$TV = '192.168.1.97'
$HomeId = 'FrameHome1.Home'
$ReturnAfterApps = $true      # $false = only open Home at power-on
$PollSeconds = 3
$Log = Join-Path $PSScriptRoot 'home-keeper.log'
$OffAfterPolls = 3            # unreachable this many polls in a row (~9 s) before the TV counts as off:
                              # a TV busy starting an app misses one, and that once sent you Home mid-switch
$BusyFile = Join-Path $PSScriptRoot '.zephyr-busy'   # Zephyr writes an end time here while she switches apps

# Every app on the Home screen (from frame-launcher\js\apps.js), by Smart Hub id.
$Apps = @('xvvl3S1TT1.TizenTubeStandalone','3201907018807','3201910019365','3201606009684','3201807016597',
          '3201710015037','3202010022079','3202008021439')

function Write-Log($msg) {
    $line = '{0:yyyy-MM-dd HH:mm:ss}  {1}' -f (Get-Date), $msg
    Add-Content -Path $Log -Value $line
    if ((Get-Item $Log).Length -gt 512KB) { Get-Content $Log -Tail 2000 | Set-Content $Log }
}

function Get-Power {
    try { (Invoke-RestMethod "http://$TV`:8001/api/v2/" -TimeoutSec 2).device.PowerState } catch { 'unreachable' }
}

function Test-Visible($id) {
    try { (Invoke-RestMethod "http://$TV`:8001/api/v2/applications/$id" -TimeoutSec 2).visible -eq $true } catch { $false }
}

$TizenTube = 'xvvl3S1TT1.TizenTubeStandalone'

function Test-Running($id) {
    try { (Invoke-RestMethod "http://$TV`:8001/api/v2/applications/$id" -TimeoutSec 2).running -eq $true } catch { $false }
}

function Test-ZephyrBusy {
    try { [double](Get-Content $BusyFile -ErrorAction Stop) -gt [double][DateTimeOffset]::UtcNow.ToUnixTimeSeconds() } catch { $false }
}

function Open-Home($why) {
    if (Test-ZephyrBusy) { Write-Log "left Home alone ($why): Zephyr is switching apps"; return }
    try {
        Invoke-WebRequest -Method Post "http://$TV`:8001/api/v2/applications/$HomeId" -TimeoutSec 4 -UseBasicParsing | Out-Null
        Write-Log "opened Home ($why)"
    } catch { Write-Log "could not open Home ($why): $($_.Exception.Message)" }
}

Write-Log 'keeper started'
$lastPower = $null
$missed = 0           # polls in a row the TV didn't answer
$lastApp = $null      # Smart Hub id of the app last seen on screen
$emptyPolls = 0

while ($true) {
    $power = Get-Power
    if ($power -eq 'unreachable' -and $lastPower -and $lastPower -ne 'unreachable') {
        $missed++
        if ($missed -lt $OffAfterPolls) { Start-Sleep -Seconds $PollSeconds; continue }   # a blip, not off
    } else { $missed = 0 }
    if ($power -ne $lastPower) {
        Write-Log "power: $lastPower -> $power"
        if ($power -eq 'on' -and $lastPower -ne $null) {
            Start-Sleep -Seconds 4
            Open-Home 'TV turned on'
            $lastApp = $null; $emptyPolls = 0
        }
        $lastPower = $power
    }

    if ($power -eq 'on' -and $ReturnAfterApps) {
        if (Test-Visible $HomeId) {
            $lastApp = $null; $emptyPolls = 0
        } else {
            $onScreen = $null
            foreach ($a in $Apps) { if (Test-Visible $a) { $onScreen = $a; break } }
            # TizenTube only reports "running" (not "visible") while a video plays: if it's running and
            # nothing else is on screen, the video is what you're watching - don't send you Home (27 Oct)
            if (-not $onScreen -and (Test-Running $TizenTube)) { $onScreen = $TizenTube }
            if ($onScreen) {
                if ($onScreen -ne $lastApp) { Write-Log "app on screen: $onScreen" }
                $lastApp = $onScreen; $emptyPolls = 0
            } elseif ($lastApp) {
                $emptyPolls++
                if ($emptyPolls -ge 2) { Open-Home "left app $lastApp"; $lastApp = $null; $emptyPolls = 0 }
            }
        }
    }

    Start-Sleep -Seconds $PollSeconds
}
