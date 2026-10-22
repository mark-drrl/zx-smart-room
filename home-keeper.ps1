# Keeps the Home launcher on The Frame: opens it when the TV turns on, and again when you leave an app.
# Runs in the background from a scheduled task. Only works while this laptop is on and on the home Wi-Fi.

$TV = '192.168.1.97'
$HomeId = 'FrameHome1.Home'
$ReturnAfterApps = $true      # $false = only open Home at power-on
$PollSeconds = 3
$Log = Join-Path $PSScriptRoot 'home-keeper.log'

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

function Open-Home($why) {
    try {
        Invoke-WebRequest -Method Post "http://$TV`:8001/api/v2/applications/$HomeId" -TimeoutSec 4 -UseBasicParsing | Out-Null
        Write-Log "opened Home ($why)"
    } catch { Write-Log "could not open Home ($why): $($_.Exception.Message)" }
}

Write-Log 'keeper started'
$lastPower = $null
$lastApp = $null      # Smart Hub id of the app last seen on screen
$emptyPolls = 0

while ($true) {
    $power = Get-Power
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
